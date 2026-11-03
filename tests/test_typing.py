"""The types a script sees: tests/typing_usage.py and the examples pass mypy and basedpyright."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
FILES = [str(ROOT / "tests" / "typing_usage.py"), str(ROOT / "examples")]


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", *args], cwd=ROOT, capture_output=True, text=True)


def test_mypy():
    pytest.importorskip("mypy")
    result = run("mypy", "--warn-unused-ignores", *FILES)
    assert result.returncode == 0, result.stdout


def test_basedpyright():
    pytest.importorskip("basedpyright")
    result = run("basedpyright", *FILES)
    assert result.returncode == 0, result.stdout
