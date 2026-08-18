#!/usr/bin/env bash
# One cheap listing per tool against the swath replay server, so an inferred
# compatibility cell becomes an observation. Reproduces RECEIPT.md.
#
# Prerequisites, in order:
#   swath list s3://noaa-ghcn-pds/parquet/by_station/ --region us-east-1 \
#     --no-sign-request --format parquet -o "$DIR/capture" --restart --concurrency 8
#   swath-replay-server sort-fixture --capture "$DIR/capture/data" --output "$DIR/sorted"
#   swath-replay-server serve --fixture "$DIR/sorted" --bucket noaa-ghcn-pds \
#     --host 0.0.0.0 --port 19090 --parquet-connections 4
#   python -m benchmark.build_image --harness-revision "$(git rev-parse HEAD)" \
#     --tag s3ls-toolbox:pilot
#
# No latency injection: this asks whether a tool can speak to the server at all,
# and answers nothing about speed.
set -u
IMG=s3ls-toolbox:pilot
EP=http://127.0.0.1:19090
B=noaa-ghcn-pds
R=us-east-1
P='parquet/by_station/STATION=ACW00011604/'
OUT=/tmp/replay-pilot/compat
DUMMY="-e AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE -e AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY -e AWS_DEFAULT_REGION=$R -e AWS_REGION=$R -e AWS_EC2_METADATA_DISABLED=true"

run() { # name, then docker args
  local name="$1"; shift
  echo "### $name"
  timeout 120 docker run --rm --network host -v "$OUT:/out" "$@" > "$OUT/$name.out" 2> "$OUT/$name.err"
  echo "exit=$? lines=$(wc -l < "$OUT/$name.out")" | tee "$OUT/$name.status"
}

run swath $DUMMY --entrypoint /opt/java/openjdk/bin/java $IMG \
  -jar /opt/swath/swath.jar -v --color never list "s3://$B/$P" --region $R \
  --no-sign-request --concurrency 4 --checkpoint none --format tsv --endpoint-url $EP

run s3p $DUMMY -e S3_ENDPOINT=$EP --entrypoint /usr/local/bin/s3p $IMG \
  ls --bucket $B --region $R --prefix "$P" --list-concurrency 4

run s3-fast-list $DUMMY --entrypoint /usr/bin/s3-fast-list $IMG \
  --no-sign-request --endpoint-url $EP --output-parquet-file /out/sfl.parquet \
  --output-ks-file /out/sfl.ks --prefix "$P" list --region $R --bucket $B

run s7cmd $DUMMY --entrypoint /usr/local/bin/s7cmd $IMG \
  ls -r -vv --disable-color-tracing --tsv --show-storage-class --show-etag \
  --max-parallel-listings 4 --target-no-sign-request --target-region $R \
  --target-endpoint-url $EP --target-force-path-style \
  --connect-timeout-milliseconds 15000 "s3://$B/$P"

# The endpoint value must be quoted inside the connection string: rclone's own
# parser otherwise splits on the URL's colons and rejects `http` as the endpoint.
run rclone $DUMMY --entrypoint /usr/local/bin/rclone $IMG \
  lsjson --fast-list --files-only --use-server-modtime --no-mimetype -R \
  ":s3,provider=AWS,region=$R,endpoint=\"$EP\",force_path_style=true:$B/$P"

printf '[default]\ns3 =\n    addressing_style = path\n' > $OUT/aws-config
run aws-cli $DUMMY -e AWS_CONFIG_FILE=/out/aws-config --entrypoint /usr/local/bin/aws $IMG \
  s3api list-objects-v2 --bucket $B --region $R --no-sign-request --prefix "$P" \
  --endpoint-url $EP --output text

run minio-mc -e MC_HOST_s3=$EP -e MC_REGION=$R --entrypoint /usr/bin/mc $IMG \
  ls --recursive "s3/$B/$P"

run s3kor $DUMMY --entrypoint /usr/local/bin/s3kor $IMG \
  ls --region $R --custom-endpoint-url $EP "s3://$B/$P"

run s5cmd $DUMMY --entrypoint /s5cmd $IMG \
  --endpoint-url $EP --no-sign-request ls -e -s "s3://$B/$P*"

run ps3 $DUMMY --entrypoint /usr/local/bin/pS3 $IMG \
  list-objects-v2 --bucket $B --region $R --endpoint-url $EP

run s4cmd $DUMMY -e AWS_CONFIG_FILE=/out/aws-config --entrypoint /usr/local/bin/s4cmd $IMG \
  ls -r -c 4 --endpoint-url $EP "s3://$B/$P"
