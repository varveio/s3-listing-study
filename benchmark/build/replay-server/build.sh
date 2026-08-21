#!/usr/bin/env bash
# Assemble a replay-server image's build context and build it.
#
# The server distribution is built from a Swath checkout. Fixture data is not a
# build input and must never be copied into this image.
#
#   SWATH_REPO=~/workspaces/swath \
#   ./build.sh us-east1-docker.pkg.dev/varve-oss/s3-listing-study/replay-server:code-only
set -euo pipefail

TAG=${1:?usage: build.sh <image-tag>}
SWATH_REPO=${SWATH_REPO:?set SWATH_REPO to a swath checkout}

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

mkdir -p "$context/dist"
cp -a "$dist/." "$context/dist/"
cp "$here/entrypoint.sh" "$context/entrypoint.sh"
chmod +x "$context/entrypoint.sh"

echo "build.sh: swath=$commit fixture=external"

docker build \
  --file "$here/Dockerfile" \
  --build-arg "SWATH_COMMIT=$commit" \
  --tag "$TAG" \
  "$context"
