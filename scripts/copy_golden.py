#!/usr/bin/env python3
"""Copies blaeck's golden scenarios and recordings into tests/golden/.

  python scripts/copy_golden.py [path to blaeck, default ../blaeck]

Copies, never edits: the scenarios and frames are blaeck's. tests/golden/SOURCE.txt records the
blaeck commit they came from, so a failing golden test can say which blaeck it was checked
against. A blaeck checkout with uncommitted golden files is refused.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tests" / "golden"


def main() -> int:
    blaeck = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT.parent / "blaeck").resolve()
    source = blaeck / "extras" / "tests" / "golden"
    if not (source / "scenarios").is_dir():
        print(f"no golden scenarios in {source}")
        return 1
    git = ["git", "-C", str(blaeck)]
    dirty = subprocess.run(git + ["status", "--porcelain", "--", "extras/tests/golden"],
                           capture_output=True, text=True, check=True).stdout.strip()
    if dirty:
        print("blaeck has uncommitted golden files; commit them first:\n" + dirty)
        return 1
    commit = subprocess.run(git + ["rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                            check=True).stdout.strip()
    for part in ("scenarios", "frames"):
        shutil.rmtree(TARGET / part, ignore_errors=True)
        shutil.copytree(source / part, TARGET / part)
    (TARGET / "SOURCE.txt").write_text(f"blaeck {commit}\n", encoding="utf-8")
    count = len(list((TARGET / "scenarios").glob("*.json")))
    print(f"copied {count} scenarios from blaeck {commit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
