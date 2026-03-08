"""File churn subprocess for dynamic loading tests.

Rapidly adds, overwrites, and deletes mbtiles files in a watched directory.
Outputs JSON lines to stdout for the parent test to parse. Designed to be
launched via subprocess.Popen and terminated with SIGTERM.
"""

import argparse
import atexit
import json
import os
import random
import shutil
import signal
import sys
import time
from datetime import datetime, timezone


def ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def log(op: str, slot: str, detail: str = ""):
    """Print a JSON line to stdout for the parent process to parse."""
    line = {"op": op, "slot": slot, "ts": ts()}
    if detail:
        line["detail"] = detail
    print(json.dumps(line), flush=True)


def cleanup(mbtiles_dir: str, slots: list[str]) -> int:
    """Remove all test_churn_* files. Returns count removed."""
    count = 0
    for slot in slots:
        p = os.path.join(mbtiles_dir, f"{slot}.mbtiles")
        try:
            os.remove(p)
            count += 1
        except FileNotFoundError:
            pass
    return count


def main():
    parser = argparse.ArgumentParser(description="File churn worker for dynamic loading tests")
    parser.add_argument("mbtiles_dir", help="Directory to churn files in")
    parser.add_argument("source_file", help="Source mbtiles file to copy from")
    parser.add_argument("--duration", type=int, default=30, help="Duration in seconds")
    parser.add_argument("--num-slots", type=int, default=5, help="Number of churn slots")
    args = parser.parse_args()

    if not os.path.isdir(args.mbtiles_dir):
        print(f"ERROR: {args.mbtiles_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    source = os.path.join(args.mbtiles_dir, args.source_file)
    if not os.path.isfile(source):
        print(f"ERROR: source file not found: {source}", file=sys.stderr)
        sys.exit(1)

    slots = [f"test_churn_{i:02d}" for i in range(1, args.num_slots + 1)]
    ops_per_sec = 10
    interval = 1.0 / ops_per_sec

    # SIGTERM sets a flag so the loop exits naturally and the summary prints
    stopping = False

    def handle_term(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, handle_term)

    # Always clean up on exit (normal, SIGTERM, atexit)
    def do_cleanup():
        removed = cleanup(args.mbtiles_dir, slots)
        if removed:
            log("cleanup", "*", f"removed {removed} files")

    atexit.register(do_cleanup)

    # Clean any leftovers from a previous run
    cleanup(args.mbtiles_dir, slots)

    alive: set[str] = set()
    counts = {"add": 0, "delete": 0, "update": 0}
    start = time.time()

    try:
        while not stopping and time.time() - start < args.duration:
            t0 = time.monotonic()
            slot = random.choice(slots)
            target = os.path.join(args.mbtiles_dir, f"{slot}.mbtiles")

            if slot not in alive:
                # ADD
                shutil.copy2(source, target)
                alive.add(slot)
                counts["add"] += 1
                log("add", slot)

            elif random.random() < 0.4:
                # DELETE
                try:
                    os.remove(target)
                except FileNotFoundError:
                    pass
                alive.discard(slot)
                counts["delete"] += 1
                log("delete", slot)

            else:
                # UPDATE — overwrite with fresh copy (triggers chokidar change event)
                shutil.copy2(source, target)
                counts["update"] += 1
                log("update", slot)

            elapsed = time.monotonic() - t0
            if elapsed < interval:
                time.sleep(interval - elapsed)

    except KeyboardInterrupt:
        pass

    # Final summary line
    total = sum(counts.values())
    print(json.dumps({
        "op": "summary",
        "slot": "*",
        "ts": ts(),
        "counts": counts,
        "total": total,
        "elapsed": round(time.time() - start, 1),
    }), flush=True)


if __name__ == "__main__":
    main()
