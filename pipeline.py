"""Profile and clean NYC Yellow Taxi trip data (January 2021) in chunks.

The pipeline stops after cleaning: cleaned rows are written to a local
csv.gz file and nothing is loaded into PostgreSQL.

Usage:
    python pipeline.py                      # download (if needed), profile, clean
    python pipeline.py --input path/to/yellow_tripdata_2021-01.csv.gz
"""

import argparse
import gzip
import hashlib
import json
import urllib.request
from pathlib import Path

import pandas as pd

DATA_URL = (
    "https://github.com/DataTalksClub/nyc-tlc-data/releases/download/"
    "yellow/yellow_tripdata_2021-01.csv.gz"
)
DATA_DIR = Path("data")
RAW_PATH = DATA_DIR / "raw" / "yellow_tripdata_2021-01.csv.gz"
CLEAN_PATH = DATA_DIR / "clean" / "yellow_tripdata_2021-01_clean.csv.gz"
REPORT_PATH = Path("reports") / "cleaning_report.json"
CHUNK_SIZE = 100_000

# Valid pickup window for a January 2021 file.
PERIOD_START = pd.Timestamp("2021-01-01")
PERIOD_END = pd.Timestamp("2021-02-01")
MAX_TRIP_DURATION = pd.Timedelta(hours=24)
# Longest plausible single yellow-cab trip; anything above is a meter error.
MAX_TRIP_DISTANCE = 100.0
# Fares above this are kept but flagged: rare, yet possible for long
# negotiated trips, so they are not proven errors.
HIGH_FARE = 500.0

REMOVAL_RULES = {
    "removed_missing_critical": "missing critical fields",
    "removed_invalid_dates": "invalid trip dates",
    "removed_non_positive_fares": "negative/zero fare or total",
    "removed_invalid_distance": "distance <= 0 or > 100 mi",
    "removed_zero_passengers": "zero passengers",
    "removed_negative_charges": "negative extras/tips/surcharges",
    "removed_duplicates": "duplicates",
}

# Surcharges and tips are never legitimately negative on a kept trip.
CHARGE_COLUMNS = [
    "extra",
    "mta_tax",
    "tip_amount",
    "tolls_amount",
    "improvement_surcharge",
    "congestion_surcharge",
]

DATE_COLUMNS = ["tpep_pickup_datetime", "tpep_dropoff_datetime"]
DTYPES = {
    "VendorID": "Int64",
    "passenger_count": "Int64",
    "trip_distance": "float64",
    "RatecodeID": "Int64",
    "store_and_fwd_flag": "string",
    "PULocationID": "Int64",
    "DOLocationID": "Int64",
    "payment_type": "Int64",
    "fare_amount": "float64",
    "extra": "float64",
    "mta_tax": "float64",
    "tip_amount": "float64",
    "tolls_amount": "float64",
    "improvement_surcharge": "float64",
    "total_amount": "float64",
    "congestion_surcharge": "float64",
}

# Rows missing any of these cannot be used and are dropped.
CRITICAL_COLUMNS = DATE_COLUMNS + [
    "PULocationID",
    "DOLocationID",
    "fare_amount",
    "total_amount",
]

# Non-critical gaps are filled with a documented default instead of dropping
# the row. Codes follow the TLC data dictionary (payment_type 5 = Unknown);
# RatecodeID 99 marks an unknown rate code.
FILL_VALUES = {
    "passenger_count": 1,
    "RatecodeID": 99,
    "store_and_fwd_flag": "N",
    "payment_type": 5,
    "congestion_surcharge": 0.0,
}


def download(url: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {url} -> {dest}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.rename(dest)
    return dest


def read_chunks(path: Path, chunk_size: int = CHUNK_SIZE):
    return pd.read_csv(
        path,
        compression="gzip",
        chunksize=chunk_size,
        dtype=DTYPES,
        parse_dates=DATE_COLUMNS,
    )


class Profiler:
    """Accumulates column-level statistics across chunks."""

    def __init__(self):
        self.rows = 0
        self.nulls: dict[str, int] = {}
        self.dtypes: dict[str, str] = {}
        self.mins: dict[str, object] = {}
        self.maxs: dict[str, object] = {}
        self.sums: dict[str, float] = {}

    def update(self, df: pd.DataFrame) -> None:
        self.rows += len(df)
        for col in df.columns:
            s = df[col]
            self.dtypes.setdefault(col, str(s.dtype))
            self.nulls[col] = self.nulls.get(col, 0) + int(s.isna().sum())
            if pd.api.types.is_numeric_dtype(s) or pd.api.types.is_datetime64_any_dtype(s):
                lo, hi = s.min(), s.max()
                if pd.notna(lo):
                    self.mins[col] = lo if col not in self.mins else min(self.mins[col], lo)
                    self.maxs[col] = hi if col not in self.maxs else max(self.maxs[col], hi)
            if pd.api.types.is_numeric_dtype(s):
                self.sums[col] = self.sums.get(col, 0.0) + float(s.sum())

    def summary(self) -> pd.DataFrame:
        cols = list(self.dtypes)
        non_null = {c: self.rows - self.nulls[c] for c in cols}
        return pd.DataFrame(
            {
                "dtype": [self.dtypes[c] for c in cols],
                "nulls": [self.nulls[c] for c in cols],
                "null_pct": [round(100 * self.nulls[c] / self.rows, 2) if self.rows else 0 for c in cols],
                "min": [self.mins.get(c) for c in cols],
                "max": [self.maxs.get(c) for c in cols],
                "mean": [
                    round(self.sums[c] / non_null[c], 3) if c in self.sums and non_null[c] else None
                    for c in cols
                ],
            },
            index=cols,
        )


def _row_hashes(df: pd.DataFrame) -> pd.Series:
    return pd.util.hash_pandas_object(df, index=False)


def clean(df: pd.DataFrame, seen_hashes: set | None = None) -> tuple[pd.DataFrame, dict]:
    """Clean one chunk and return (cleaned_df, metrics).

    Rules are applied in order so every removed row is counted exactly once:
      1. missing critical fields
      2. invalid trip dates (pickup outside Jan 2021, dropoff not after
         pickup, or trip longer than 24h)
      3. negative or zero fare_amount / total_amount
      4. trip_distance <= 0 or above MAX_TRIP_DISTANCE
      5. passenger_count of 0 (missing counts are filled, not dropped)
      6. negative extras, taxes, tips, tolls or surcharges
      7. duplicates (within the chunk, and across chunks if seen_hashes given)
    Non-critical missing values are filled using FILL_VALUES.
    """
    metrics = {"input_rows": len(df)}

    missing = df[CRITICAL_COLUMNS].isna().any(axis=1)
    metrics["removed_missing_critical"] = int(missing.sum())
    df = df[~missing]

    pickup, dropoff = df["tpep_pickup_datetime"], df["tpep_dropoff_datetime"]
    bad_dates = (
        (pickup < PERIOD_START)
        | (pickup >= PERIOD_END)
        | (dropoff <= pickup)
        | ((dropoff - pickup) > MAX_TRIP_DURATION)
    )
    metrics["removed_invalid_dates"] = int(bad_dates.sum())
    df = df[~bad_dates]

    bad_fares = (df["fare_amount"] <= 0) | (df["total_amount"] <= 0)
    metrics["removed_non_positive_fares"] = int(bad_fares.sum())
    df = df[~bad_fares]

    bad_distance = (df["trip_distance"] <= 0) | (df["trip_distance"] > MAX_TRIP_DISTANCE)
    metrics["removed_invalid_distance"] = int(bad_distance.sum())
    df = df[~bad_distance]

    zero_passengers = df["passenger_count"].eq(0).fillna(False).astype(bool)
    metrics["removed_zero_passengers"] = int(zero_passengers.sum())
    df = df[~zero_passengers]

    negative_charges = (df[CHARGE_COLUMNS] < 0).any(axis=1)
    metrics["removed_negative_charges"] = int(negative_charges.sum())
    df = df[~negative_charges]

    hashes = _row_hashes(df)
    dupes = hashes.duplicated()
    if seen_hashes is not None:
        dupes |= hashes.isin(seen_hashes)
        seen_hashes.update(hashes[~dupes].tolist())
    metrics["removed_duplicates"] = int(dupes.sum())
    df = df[~dupes.to_numpy()]

    df = df.copy()
    for col, value in FILL_VALUES.items():
        if col in df.columns:
            n = int(df[col].isna().sum())
            metrics[f"filled_{col}"] = n
            if n:
                df[col] = df[col].fillna(value)

    # Flags keep the row but mark it for anyone using the data downstream.
    df["flag_missing_vendor"] = df["VendorID"].isna()
    df["flag_high_fare"] = df["fare_amount"] > HIGH_FARE
    metrics["flagged_missing_vendor"] = int(df["flag_missing_vendor"].sum())
    metrics["flagged_high_fare"] = int(df["flag_high_fare"].sum())

    metrics["output_rows"] = len(df)
    metrics["removed_total"] = metrics["input_rows"] - metrics["output_rows"]
    return df, metrics


def run(input_path: Path, output_path: Path, report_path: Path, chunk_size: int) -> dict:
    raw_profile, clean_profile = Profiler(), Profiler()
    totals: dict[str, int] = {}
    seen_hashes: set = set()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(output_path, "wt", newline="") as out:
        for i, chunk in enumerate(read_chunks(input_path, chunk_size)):
            raw_profile.update(chunk)
            cleaned, metrics = clean(chunk, seen_hashes)
            clean_profile.update(cleaned)
            cleaned.to_csv(out, index=False, header=(i == 0))
            for k, v in metrics.items():
                totals[k] = totals.get(k, 0) + v
            print(
                f"chunk {i:>3}: in={metrics['input_rows']:>7,} "
                f"out={metrics['output_rows']:>7,} removed={metrics['removed_total']:>6,}"
            )

    raw_summary = raw_profile.summary()
    clean_summary = clean_profile.summary()
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print("\n=== Raw data profile ===")
        print(raw_summary.to_string())
        print("\n=== Cleaned data profile ===")
        print(clean_summary.to_string())

    print("\n=== Cleaning metrics ===")
    for k, v in totals.items():
        print(f"{k:<32}{v:>12,}")

    removed = {label: totals[key] for key, label in REMOVAL_RULES.items()}
    print("\n=== {rule: rows_removed} ===")
    print(removed)

    rows, before = [], totals["input_rows"]
    for label, n in removed.items():
        rows.append({"rule": label, "rows_before": before, "rows_removed": n, "rows_after": before - n})
        before -= n
    rule_table = pd.DataFrame(rows)
    print("\n=== Rows per rule, before and after ===")
    print(rule_table.to_string(index=False))

    # Verification: every row is either kept or removed by exactly one rule,
    # and the file on disk holds exactly the rows we counted.
    assert totals["input_rows"] - sum(removed.values()) == totals["output_rows"]
    with gzip.open(output_path, "rt") as f:
        written = sum(1 for _ in f) - 1
    assert written == totals["output_rows"], (written, totals["output_rows"])
    print(f"\nVerified: {totals['input_rows']:,} in - {sum(removed.values()):,} removed "
          f"= {totals['output_rows']:,} out; file on disk has {written:,} rows")

    report = {
        "input_file": str(input_path),
        "output_file": str(output_path),
        "chunk_size": chunk_size,
        "metrics": totals,
        "raw_profile": json.loads(raw_summary.to_json(orient="index", date_format="iso")),
        "clean_profile": json.loads(clean_summary.to_json(orient="index", date_format="iso")),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2))
    print(f"\nCleaned data: {output_path}\nReport:       {report_path}")
    print("Stopped at the cleaning stage (no PostgreSQL load).")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, help="local yellow_tripdata_2021-01.csv.gz")
    parser.add_argument("--output", type=Path, default=CLEAN_PATH)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--chunk-size", type=int, default=CHUNK_SIZE)
    args = parser.parse_args()

    input_path = args.input or download(DATA_URL, RAW_PATH)
    run(input_path, args.output, args.report, args.chunk_size)


if __name__ == "__main__":
    main()
