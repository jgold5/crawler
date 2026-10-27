"""The installed package must contain every data file the code loads (tests run from source and won't notice)."""
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def _clean_copy(dst: Path) -> Path:
    """Copy the source tree as git sees it (no stale build/ or egg-info that would mask missing files)."""
    files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                           capture_output=True, text=True, check=True).stdout.splitlines()
    for rel in files:
        src = ROOT / rel
        if src.is_file():
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            (dst / rel).write_bytes(src.read_bytes())
    return dst


def test_wheel_contains_all_package_data(tmp_path):
    src = _clean_copy(tmp_path / "src")
    out = tmp_path / "dist"
    # Isolated build, like `pip install` / `pipx install` on the user's machine (fetches setuptools).
    r = subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w", str(out), str(src)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        if "Could not fetch" in r.stderr or "No matching distribution" in r.stderr or "connection" in r.stderr.lower():
            pytest.skip("no network to fetch build dependencies")
        raise AssertionError(r.stderr[-2000:])
    wheel = next(out.glob("crawler-*.whl"))
    names = set(zipfile.ZipFile(wheel).namelist())
    expected = {p.relative_to(ROOT).as_posix() for p in (ROOT / "crawler").rglob("*")
                if p.is_file() and p.suffix not in (".py", ".pyc") and "__pycache__" not in p.parts}
    missing = sorted(expected - names)
    assert not missing, f"not shipped in the wheel: {missing}"
