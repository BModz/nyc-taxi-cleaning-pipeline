# NYC Yellow Taxi (Jan 2021): Profiling & Cleaning Pipeline

## Overview & Instructions Followed

* Analyzed and profiled `yellow_tripdata_2021-01.csv.gz` in chunks of 100,000 rows.
* Handled missing values, invalid trip dates, negative/zero fares, and duplicates.
* Applied custom `clean(df)` logic returning cleaned data and removed-row metrics.
* Pipeline stopped at the cleaning stage without loading into PostgreSQL.

No data files, `.venv` directory or keys are tracked in this repository; credentials live in a git-ignored `.env`.

## Files

| File | Purpose |
|---|---|
| `ingest_data.py` | Loads the CSV into Postgres in 100,000-row chunks (replace on the first chunk, then append) and checks the row count |
| `Dockerfile` | Builds the `taxi_ingest:v001` image that runs `ingest_data.py` |
| `docker-compose.yaml` | Postgres (`pgdatabase`) and pgAdmin on the `pg-network` network |
| `pipeline.py` | Chunked profiling and `clean(df)`; stops before any database load |
| `report/build_report.py` | Builds the PDF report from the captured terminal output in `evidence/` |

## Load into Postgres with Docker

```bash
cp .env.example .env            # then change the passwords
docker compose up -d --wait
docker build -t taxi_ingest:v001 .
docker run --rm --network=pg-network --env-file .env taxi_ingest:v001 --host=pgdatabase --table=yellow_taxi_trips --url=https://github.com/DataTalksClub/nyc-tlc-data/releases/download/yellow/yellow_tripdata_2021-01.csv.gz
docker compose exec pgdatabase psql -U root -d ny_taxi -c "SELECT COUNT(*) FROM yellow_taxi_trips;"
```

The count should be 1,369,765. pgAdmin runs at http://localhost:8085.

## Setup (cleaning pipeline)

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows  (source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
```

## Run

```bash
python pipeline.py
```

The script downloads the source file (from the
[DataTalksClub nyc-tlc-data](https://github.com/DataTalksClub/nyc-tlc-data/releases/tag/yellow)
release) into `data/raw/` if it is not already there. To use a local copy:

```bash
python pipeline.py --input path/to/yellow_tripdata_2021-01.csv.gz
```

Outputs:

| Path | Contents |
|---|---|
| `data/clean/yellow_tripdata_2021-01_clean.csv.gz` | Cleaned rows (not committed) |
| `reports/cleaning_report.json` | Removed-row metrics plus raw and cleaned column profiles |

## Cleaning rules (`clean(df)`)

`clean(df, seen_hashes=None)` returns `(cleaned_df, metrics)`. Rules run in
order, so each removed row is counted once:

1. **Missing critical fields:** drop rows missing pickup/dropoff time,
   pickup/dropoff location, `fare_amount` or `total_amount`.
2. **Invalid trip dates:** drop rows whose pickup is outside January 2021,
   whose dropoff is not after the pickup, or whose trip lasts over 24 hours.
3. **Negative/zero fares:** drop rows with `fare_amount <= 0` or `total_amount <= 0`.
4. **Distance outliers:** drop rows with `trip_distance <= 0` or above 100 miles.
5. **Zero passengers:** drop rows with `passenger_count == 0` (missing counts
   are filled, not dropped).
6. **Negative charges:** drop rows where `extra`, `mta_tax`, `tip_amount`,
   `tolls_amount`, `improvement_surcharge` or `congestion_surcharge` is negative.
7. **Duplicates:** drop exact duplicate rows within a chunk and, through a
   shared set of row hashes, across chunks.

Rows with `VendorID` missing are flagged (`flag_missing_vendor`), as are fares over 500 (`flag_high_fare`).

Non-critical gaps are filled instead of dropped: `passenger_count -> 1`,
`RatecodeID -> 99` (unknown), `store_and_fwd_flag -> "N"`,
`payment_type -> 5` (Unknown in the TLC dictionary),
`congestion_surcharge -> 0`. `VendorID` is left null because there is no
sensible default.

## Results

| Metric | Rows |
|---|---:|
| Input rows | 1,369,765 |
| Removed: missing critical fields | 0 |
| Removed: invalid trip dates | 6,586 |
| Removed: negative/zero fares | 7,343 |
| Removed: distance <= 0 or > 100 mi | 18,362 |
| Removed: zero passengers | 26,242 |
| Removed: negative extras/tips/surcharges | 2 |
| Removed: duplicates | 0 |
| **Output rows** | **1,311,230** |
| Rows with filled non-critical values | 91,241 |

Most rows with negative extras or tips were refunds that rule 3 had already
removed, which is why rule 6 removes only 2.

Raw-data observations from profiling:

* 98,352 rows (7.18%) have no `VendorID`, `passenger_count`, `RatecodeID`,
  `store_and_fwd_flag` or `payment_type`; they come as one block.
* Pickup times run from 2008-12-31 to 2021-02-22, outside the file's month.
* `fare_amount` ranges from -490.00 to 6,960.50; `total_amount` from -492.80 to 7,661.28.
* `trip_distance` reaches 263,163 miles, which is clearly an outlier.
* 26,242 trips record 0 passengers.
* After cleaning, `fare_amount` still peaks at 6,960.50. These very high fares are kept because there is no cap rule for them.
