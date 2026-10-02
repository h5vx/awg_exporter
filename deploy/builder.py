"""
Build the awg-exporter binary with build.sh before deploying.

pyinfra executes the deploy file once per host, concurrently (gevent with
monkey-patched threading), while this module is imported only once. The lock
and `_build_rc` make a multi-host run build a single time; other hosts wait
for that build and share its result.
"""

import subprocess
import sys
import threading
from pathlib import Path

MODES = ("auto", "always", "never")

# Files whose changes require a rebuild
SOURCE_GLOBS = ("**/*.go", "go.mod", "go.sum", "Dockerfile", "build.sh")

_lock = threading.Lock()
_build_rc = None  # exit code of build.sh once it has run


def _is_stale(repo_dir, binary):
    if not binary.is_file():
        return True
    binary_mtime = binary.stat().st_mtime
    return any(
        path.stat().st_mtime > binary_mtime
        for pattern in SOURCE_GLOBS
        for path in repo_dir.glob(pattern)
        if ".git" not in path.parts
    )


def ensure_binary(repo_dir, binary, mode):
    """Rebuild `binary` according to `mode` (auto/always/never)."""
    global _build_rc

    if mode not in MODES:
        raise SystemExit(f"build must be one of {', '.join(MODES)}, got {mode!r}")
    if mode == "never":
        return
    if binary != repo_dir / "awg-exporter":
        raise SystemExit(f"build.sh produces {repo_dir / 'awg-exporter'}; set build=never to deploy {binary}")

    with _lock:
        if _build_rc is None and (mode == "always" or _is_stale(repo_dir, binary)):
            print("--> Building awg-exporter with build.sh", file=sys.stderr, flush=True)
            _build_rc = subprocess.run([str(repo_dir / "build.sh")], cwd=repo_dir, stdout=sys.stderr).returncode

    if _build_rc:
        raise SystemExit(f"build.sh failed with exit code {_build_rc}")
