#!/usr/bin/env bash
# The daily run: find new CBSL price reports, download them, and load silver.
#
# CBSL blocks GitHub's servers, so this runs on a local machine. It is safe to
# run more than once a day: every step only does what is still missing.
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"
cd "$(dirname "$0")/.."

# Exits non-zero if a download failed or a report was rejected; the next run
# retries the download, and `lk-prices stats` lists the rejects.
uv run lk-prices run
