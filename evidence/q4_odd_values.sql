SELECT
  COUNT(*) FILTER (WHERE fare_amount <= 0 OR total_amount <= 0)        AS non_pos_fare,
  COUNT(*) FILTER (WHERE trip_distance <= 0)                           AS zero_distance,
  COUNT(*) FILTER (WHERE trip_distance > 100)                          AS over_100mi,
  COUNT(*) FILTER (WHERE passenger_count = 0)                          AS zero_pax,
  COUNT(*) FILTER (WHERE extra < 0 OR mta_tax < 0 OR tip_amount < 0 OR tolls_amount < 0
                      OR improvement_surcharge < 0 OR congestion_surcharge < 0) AS neg_charges
FROM yellow_taxi_trips;
SELECT COUNT(*) AS duplicate_rows FROM (
  SELECT COUNT(*) AS n FROM yellow_taxi_trips t GROUP BY t.* HAVING COUNT(*) > 1
) d;
