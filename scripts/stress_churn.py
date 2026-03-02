# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Stress test: file churner for the chokidar data watcher.

Rapidly adds, overwrites, and deletes a fixed set of 50 .mbtiles files
by copying real source files from the same directory. Targets ~10 ops/sec.
Run alongside stress_requests.py which hammers these same files via HTTP.

Setup:
  The mbtiles directory must contain real .mbtiles files under 2MB
  (e.g. airnow_*, aqi_* files) which are used as copy sources.

Usage:
  uv run scripts/stress_churn.py <mbtiles_dir> [duration_seconds]

Example:
  uv run scripts/stress_churn.py /media/jeremy/bigboy/tile-splitter/tile-server/mbtiles 120
"""

import glob
import os
import random
import shutil
import sys
import time
from datetime import datetime, timezone

MBTILES_DIR = sys.argv[1] if len(sys.argv) > 1 else "/data/mbtiles"
DURATION = int(sys.argv[2]) if len(sys.argv) > 2 else 60

SLOTS = [f"stresstest_{i:02d}" for i in range(1, 51)]
MAX_SOURCE_SIZE = 2 * 1024 * 1024
OPS_PER_SEC = 10


def ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def find_sources(d: str) -> list[str]:
    """Find real .mbtiles files under 2MB to use as copy sources."""
    out = []
    for f in glob.glob(os.path.join(d, "*.mbtiles")):
        if os.path.basename(f).startswith("stresstest_"):
            continue
        try:
            if 0 < os.path.getsize(f) <= MAX_SOURCE_SIZE:
                out.append(f)
        except OSError:
            pass
    return sorted(out)


def cleanup(d: str) -> int:
    """Remove all stresstest_* files."""
    count = 0
    for slot in SLOTS:
        p = os.path.join(d, f"{slot}.mbtiles")
        try:
            os.remove(p)
            count += 1
        except FileNotFoundError:
            pass
    return count


def main():
    if not os.path.isdir(MBTILES_DIR):
        print(f"ERROR: {MBTILES_DIR} is not a directory", file=sys.stderr)
        sys.exit(1)

    sources = find_sources(MBTILES_DIR)
    if len(sources) < 2:
        print(
            f"ERROR: Need at least 2 source .mbtiles files under "
            f"{MAX_SOURCE_SIZE // 1024}KB in {MBTILES_DIR}, found {len(sources)}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"[{ts()}] === Churn Stress Test ===")
    print(f"  Directory:   {MBTILES_DIR}")
    print(f"  Duration:    {DURATION}s")
    print(f"  Slots:       {len(SLOTS)} ({SLOTS[0]} .. {SLOTS[-1]})")
    print(f"  Target rate: ~{OPS_PER_SEC} ops/sec")
    print(f"  Sources:     {len(sources)} files:")
    for s in sources[:8]:
        print(f"    {os.path.basename(s):40s} {os.path.getsize(s) / 1024:6.0f}KB")
    if len(sources) > 8:
        print(f"    ... and {len(sources) - 8} more")

    removed = cleanup(MBTILES_DIR)
    if removed:
        print(f"[{ts()}] Cleaned up {removed} leftover files")

    # Track which slots currently have a file on disk
    alive: dict[str, str] = {}  # slot name -> source basename
    ops = {"add": 0, "overwrite": 0, "delete": 0}
    start = time.time()
    interval = 1.0 / OPS_PER_SEC

    print(f"[{ts()}] Starting churn (Ctrl+C to stop)...\n")
    try:
        while time.time() - start < DURATION:
            t0 = time.monotonic()
            slot = random.choice(SLOTS)
            target = os.path.join(MBTILES_DIR, f"{slot}.mbtiles")

            if slot not in alive:
                # ADD — copy a source file into the slot
                src = random.choice(sources)
                shutil.copy2(src, target)
                alive[slot] = os.path.basename(src)
                ops["add"] += 1
                print(f"[{ts()}] + ADD      {slot} <- {alive[slot]}")

            elif random.random() < 0.4:
                # DELETE — remove the file
                try:
                    os.remove(target)
                except FileNotFoundError:
                    pass
                old = alive.pop(slot)
                ops["delete"] += 1
                print(f"[{ts()}] - DELETE   {slot} (was {old})")

            else:
                # OVERWRITE — replace with a different source file
                old_src = alive[slot]
                candidates = [s for s in sources if os.path.basename(s) != old_src]
                src = random.choice(candidates)
                shutil.copy2(src, target)
                alive[slot] = os.path.basename(src)
                ops["overwrite"] += 1
                print(f"[{ts()}] ~ OVERWRT  {slot} ({old_src} -> {alive[slot]})")

            # Sleep remaining time to hit target ops/sec
            elapsed = time.monotonic() - t0
            if elapsed < interval:
                time.sleep(interval - elapsed)

    except KeyboardInterrupt:
        print(f"\n[{ts()}] Interrupted!")

    print(f"\n[{ts()}] Cleaning up {len(alive)} remaining files...")
    cleanup(MBTILES_DIR)
    elapsed = time.time() - start
    total = sum(ops.values())
    print(f"[{ts()}] Done. {elapsed:.1f}s, {total} ops ({total / max(elapsed, 0.1):.1f}/s)")
    print(f"  add={ops['add']}  overwrite={ops['overwrite']}  delete={ops['delete']}")


if __name__ == "__main__":
    main()
