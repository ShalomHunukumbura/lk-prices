#!/usr/bin/env bash
# The daily run: find new CBSL price reports, load them, rebuild gold and the
# page's data, and push that data if it changed.
#
# CBSL blocks GitHub's servers, so this runs on a local machine. It is safe to
# run more than once a day: every step only does what is still missing.
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"
cd "$(dirname "$0")/.."

git pull --quiet --ff-only

# Exits non-zero if a download failed or a report was rejected; the next run
# retries the download, and `lk-prices stats` lists the rejects. Nothing is
# published from a failed run.
uv run lk-prices run

# Publish the page's data if it changed. Pushing triggers the pages workflow.
# Only site/data is committed, never other work in progress, and only once the
# page itself is in the repository.
if ! git ls-files --error-unmatch site/index.html >/dev/null 2>&1; then
    echo "The page isn't committed yet; not publishing its data."
    exit 0
fi
git add site/data
if git diff --cached --quiet -- site/data; then
    echo "No change to the page data."
else
    git commit --quiet -m "data: reports up to $(date +%F)" -- site/data
    git push --quiet
    echo "Pushed new page data."
fi

# Sundays: compact the week's small files and vacuum what's past the retention.
if [ "$(date +%u)" = 7 ]; then
    uv run lk-prices maintain
fi
