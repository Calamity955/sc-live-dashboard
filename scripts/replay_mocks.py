#!/usr/bin/env python3
"""Replay loop: ogni N secondi sostituisce data/csv/current.csv col prossimo snapshot."""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CSV_DIR = ROOT / "data" / "csv"
CURRENT = CSV_DIR / "current.csv"

INTERVAL = float(os.getenv("REPLAY_INTERVAL", "10"))


def list_snapshots() -> list[Path]:
    return sorted(CSV_DIR.glob("tp_snapshot_*.csv"))


def main() -> int:
    snaps = list_snapshots()
    if not snaps:
        print(f"ERROR: no snapshots in {CSV_DIR}. Run generate_mock_scenario.py first.", file=sys.stderr)
        return 1

    print(f"Replay loop: {len(snaps)} snapshots, interval={INTERVAL}s -> {CURRENT}")
    idx = 0
    while True:
        src = snaps[idx % len(snaps)]
        shutil.copyfile(src, CURRENT)
        # tocca mtime per sicurezza
        os.utime(CURRENT, None)
        print(f"[{time.strftime('%H:%M:%S')}] swap -> {src.name}")
        idx += 1
        if idx % len(snaps) == 0:
            print(f"[{time.strftime('%H:%M:%S')}] loop restart")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        sys.exit(0)
