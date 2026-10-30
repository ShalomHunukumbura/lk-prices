"""Table maintenance: compaction, checkpoints, vacuum and log cleanup.

Every MERGE writes new Parquet files and leaves the old ones on disk, because
older table versions still point to them. Twice a day for a year, that's
hundreds of small files in the current year's partition, and every read has to
open all of them. Maintenance, run weekly:

- compact: rewrite each partition's small files into a few large ones. It's a
  new table version with the same rows, so readers see no change.
- checkpoint: write the table state as one Parquet file, so opening the table
  reads that and a few JSON commits instead of replaying the whole log.
- vacuum: delete files no version within RETENTION still needs.
- cleanup: delete log entries older than RETENTION (Delta's own default).

RETENTION is how far back time travel works: `lk-prices gold --version N` can
rebuild gold from any silver version from the last 30 days. Older versions'
files are gone after a vacuum; that is the trade for not keeping every file
ever written.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from deltalake import DeltaTable

from .bronze import DATA_DIR

RETENTION = timedelta(days=30)
TABLES = (
    "bronze/reports",
    "silver/prices",
    "silver/rejects",
    "silver/quarantine",
    "gold/index",
    "gold/monthly",
    "gold/margins",
)


@dataclass(frozen=True)
class Result:
    table: str
    files_before: int
    files_after: int
    compacted: int  # small files rewritten
    vacuumed: int  # files deleted from disk


def _data_files(path: Path) -> int:
    return sum(1 for p in path.rglob("*.parquet") if "_delta_log" not in p.parts)


def maintain(path: Path, name: str, retention: timedelta = RETENTION) -> Result:
    """Compact, checkpoint, vacuum and clean up one table.

    A retention shorter than Delta's 7-day default is only for experiments on a
    copy (scripts/bench_maintenance.py): a reader still on an old version would
    find its files gone.
    """
    before = _data_files(path)
    table = DeltaTable(str(path))
    metrics = table.optimize.compact()
    table.create_checkpoint()
    deleted = table.vacuum(
        retention_hours=int(retention.total_seconds() // 3600),
        dry_run=False,
        enforce_retention_duration=retention >= timedelta(days=7),
    )
    table.cleanup_metadata()
    return Result(
        table=name,
        files_before=before,
        files_after=_data_files(path),
        compacted=metrics["numFilesRemoved"],
        vacuumed=len(deleted),
    )


def maintain_all(root: Path = DATA_DIR, retention: timedelta = RETENTION) -> list[Result]:
    results = []
    for name in TABLES:
        path = root / name
        if DeltaTable.is_deltatable(str(path)):
            results.append(maintain(path, name, retention))
    return results
