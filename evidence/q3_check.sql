SELECT
  COUNT(*) FILTER (WHERE "VendorID" IS NULL) AS null_vendor,
  COUNT(*) FILTER (WHERE tpep_pickup_datetime <  '2021-01-01'
                      OR tpep_pickup_datetime >= '2021-02-01'
                      OR tpep_dropoff_datetime <= tpep_pickup_datetime
                      OR tpep_dropoff_datetime - tpep_pickup_datetime > INTERVAL '24 hours') AS bad_dates,
  MIN(tpep_pickup_datetime) AS min_pickup,
  MAX(tpep_pickup_datetime) AS max_pickup
FROM yellow_taxi_trips;
