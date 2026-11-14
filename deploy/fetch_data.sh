#!/usr/bin/env bash
# Render build step: download the FIRMS data files that are kept out of Git (published as
# assets of the GitHub release "data-2026-11-13") and verify each against deploy/data.sha256.
set -euo pipefail
cd "$(dirname "$0")/.."
BASE="https://github.com/ziadatta1/FireSync/releases/download/data-2026-11-13"
while read -r sum path; do
  asset="$(echo "$path" | sed 's#^data/##; s#/#__#g')"   # data/global/modis/observations.csv -> global__modis__observations.csv
  mkdir -p "$(dirname "$path")"
  curl -fsSL --http1.1 --retry 5 --retry-all-errors --retry-delay 3 -o "$path" "$BASE/$asset"
done < deploy/data.sha256
sha256sum -c deploy/data.sha256
