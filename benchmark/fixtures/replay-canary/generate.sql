COPY (
  SELECT
    encode(printf('group-%02d/object-%06d.dat', i // 128, i)) AS key,
    (1024 + i)::BIGINT AS size,
    TIMESTAMP '2026-01-01 00:00:00' + i * INTERVAL 1 SECOND AS last_modified,
    printf('%032x', i) AS etag,
    'STANDARD'::VARCHAR AS storage_class,
    NULL::VARCHAR AS version_id,
    NULL::BOOLEAN AS is_latest,
    NULL::BOOLEAN AS is_delete_marker,
    NULL::VARCHAR AS owner_id,
    NULL::VARCHAR AS owner_display_name,
    NULL::VARCHAR AS checksum_algorithm,
    NULL::VARCHAR AS checksum_type,
    'OBJECT'::VARCHAR AS row_type
  FROM range(0, 2048) AS rows(i)
  ORDER BY key
) TO 'benchmark/fixtures/replay-canary/part-00000.parquet'
  (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 1024);
