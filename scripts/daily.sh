#!/usr/bin/env bash
# The daily run: fetch today's prices into bronze, then load silver.
#
# Prices can't be fetched for a day that has passed, so a missed day is a gap
# for good. The timer is Persistent, so a laptop that was off at the scheduled
# time catches up as soon as it wakes.
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"
cd "$(dirname "$0")/.."

# Exits non-zero if categories failed or a check stopped the load. Bronze is
# resumable, so running this again only fetches what is missing.
uv run lk-prices run
