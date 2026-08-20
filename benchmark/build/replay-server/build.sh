#!/usr/bin/env bash
# Assemble a replay-server image's build context and build it.
#
# Neither input is in this repository: the server distribution is built from a
# swath checkout, and the fixture is a listing capture the study does not commit.
# This script records how the two are put together, so a receipt can name
# exactly what went in.
#
#   SWATH_REPO=~/workspaces/swath \
#   FIXTURE_DIR=~/work/sorel-replay/sorted \
#   FIXTURE_BUCKET=sorel-20m \
#   ./build.sh us-east1-docker.pkg.dev/varve-oss/s3-listing-study/replay-server:sorel-20m
set -euo pipefail

TAG=${1:?usage: build.sh <image-tag>}
SWATH_REPO=${SWATH_REPO:?set SWATH_REPO to a swath checkout}
FIXTURE_DIR=${FIXTURE_DIR:?set FIXTURE_DIR to a directory of sorted *.parquet parts}
FIXTURE_BUCKET=${FIXTURE_BUCKET:?set FIXTURE_BUCKET to the bucket name the server should answer for}

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
context=$(mktemp -d)
trap 'rm -rf -- "$context"' EXIT

dist="$SWATH_REPO/swath-replay-server/build/install/swath-replay-server"
[[ -x $dist/bin/swath-replay-server ]] || {
  echo "build.sh: no installDist at $dist — run :swath-replay-server:installDist first" >&2
  exit 1
}

commit=$(git -C "$SWATH_REPO" rev-parse HEAD)
dirty=$(git -C "$SWATH_REPO" status --porcelain | wc -l)
[[ $dirty -eq 0 ]] || {
  echo "build.sh: swath checkout is dirty; a replay image must name a clean commit" >&2
  exit 1
}

mkdir -p "$context/dist" "$context/fixture/$FIXTURE_BUCKET"
cp -a "$dist/." "$context/dist/"
cp -a "$FIXTURE_DIR"/*.parquet "$context/fixture/$FIXTURE_BUCKET/"
cp "$here/entrypoint.sh" "$context/entrypoint.sh"
chmod +x "$context/entrypoint.sh"

# One digest over the served bytes, in the parts' sorted order: what the server
# answered from, independent of how many files it took to say it.
fixture_sha=$(find "$context/fixture/$FIXTURE_BUCKET" -name '*.parquet' -print0 \
  | sort -z | xargs -0 cat | sha256sum | cut -d' ' -f1)

echo "build.sh: swath=$commit fixture_sha256=$fixture_sha bucket=$FIXTURE_BUCKET"

docker build \
  --file "$here/Dockerfile" \
  --build-arg "SWATH_COMMIT=$commit" \
  --build-arg "FIXTURE_SHA256=$fixture_sha" \
  --build-arg "FIXTURE_BUCKET=$FIXTURE_BUCKET" \
  --tag "$TAG" \
  "$context"
