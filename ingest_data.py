"""Load NYC Yellow Taxi CSV files into PostgreSQL in chunks.

Usage (inside Docker, on the compose network):
    docker run --rm --network=pg-network --env-file .env taxi_ingest:v001 \
        --host=pgdatabase --table=yellow_taxi_trips \
        --url=https://github.com/DataTalksClub/nyc-tlc-data/releases/download/yellow/yellow_tripdata_2021-01.csv.gz

Several --url values can be given to load many months into one table: the
table is replaced once, before the first chunk of the first file, and every
other chunk is appended.
"""

import argparse
import os
import time
import urllib.request
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

CHUNK_SIZE = 100_000
DATE_COLUMNS = ["tpep_pickup_datetime", "tpep_dropoff_datetime"]

# Int64 (capital I) is pandas' nullable integer. Plain int64 cannot hold
# NaN, so columns with gaps (VendorID, passenger_count, ...) would silently
# become float64 and be created as DOUBLE PRECISION in Postgres.
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


def download(url: str, dest_dir: Path) -> Path:
    dest = dest_dir / url.rsplit("/", 1)[-1]
    if not dest.exists():
        print(f"Downloading {url}")
        urllib.request.urlretrieve(url, dest)
    return dest


def ingest(engine, table: str, paths: list[Path], chunk_size: int) -> int:
    total = 0
    first_chunk = True
    for path in paths:
        chunks = pd.read_csv(
            path,
            compression="infer",
            chunksize=chunk_size,
            dtype=DTYPES,
            parse_dates=DATE_COLUMNS,
        )
        for i, chunk in enumerate(chunks):
            start = time.perf_counter()
            if first_chunk:
                # Create (or reset) the table from the schema alone, then
                # append every chunk, including this one.
                chunk.head(0).to_sql(table, engine, if_exists="replace", index=False)
                first_chunk = False
            chunk.to_sql(table, engine, if_exists="append", index=False, method="multi", chunksize=10_000)
            total += len(chunk)
            print(
                f"{path.name} chunk {i:>2}: inserted {len(chunk):>7,} rows "
                f"(total {total:>9,}) in {time.perf_counter() - start:.1f}s"
            )
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest CSV data into PostgreSQL")
    parser.add_argument("--user", default=os.getenv("POSTGRES_USER", "root"))
    parser.add_argument("--password", default=os.getenv("POSTGRES_PASSWORD"))
    parser.add_argument("--host", default=os.getenv("POSTGRES_HOST", "localhost"))
    parser.add_argument("--port", default=os.getenv("POSTGRES_PORT", "5432"))
    parser.add_argument("--db", default=os.getenv("POSTGRES_DB", "ny_taxi"))
    parser.add_argument("--table", default="yellow_taxi_trips")
    parser.add_argument("--url", action="append", required=True, help="CSV URL; repeat for more months")
    parser.add_argument("--chunk-size", type=int, default=CHUNK_SIZE)
    parser.add_argument("--data-dir", type=Path, default=Path("/tmp"))
    args = parser.parse_args()

    if not args.password:
        parser.error("set --password or POSTGRES_PASSWORD")

    engine = create_engine(
        f"postgresql://{args.user}:{args.password}@{args.host}:{args.port}/{args.db}"
    )
    paths = [download(url, args.data_dir) for url in args.url]
    total = ingest(engine, args.table, paths, args.chunk_size)

    with engine.connect() as conn:
        db_count = conn.execute(text(f'SELECT COUNT(*) FROM "{args.table}"')).scalar()
    print(f"Rows read: {total:,} | rows in {args.table}: {db_count:,}")
    if db_count != total:
        raise SystemExit("Row count mismatch between source and database")


if __name__ == "__main__":
    main()
