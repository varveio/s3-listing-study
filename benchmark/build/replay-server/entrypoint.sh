#!/usr/bin/env bash
# The harness owns all replay controls; do not add unrecorded startup modes.
exec /opt/swath-replay-server/bin/swath-replay-server "$@"
