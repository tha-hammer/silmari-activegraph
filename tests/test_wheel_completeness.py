"""Wheel-completeness gate. CONTRACT v1.1 #8.

Builds the wheel from the current source tree, installs it into a
fresh venv (NOT editable; the whole point is to test the wheel
artifact), and runs the quickstart against the installed package.
If any runtime data file is missing from the wheel, the quickstart
crashes with `PackPromptLoadError` (the v1.0-rc2 B3 failure shape)
or analog, and this test fails.

The smoke command is `activegraph quickstart` — the v1.0 spec
(CONTRACT v1.0 #1), fixture-backed, no network, no API key. It
exercises the diligence pack end-to-end, which loads all 4
prompts from the wheel.

Marked `slow` so local `pytest` doesn't pay the build cost on
every invocation. CI invokes this test explicitly via
`pytest -m slow tests/test_wheel_completeness.py`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import venv
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.slow
def test_wheel_installs_and_quickstart_runs() -> None:
    """Build the wheel, install it in a fresh venv, run quickstart.

    The gate is the absence of a crash. A successful exit from
    `activegraph quickstart` (return code 0) proves every runtime
    data file the quickstart touches is present in the wheel.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dist_dir = tmp_path / "dist"
        venv_dir = tmp_path / "venv"

        # 1. Build the wheel from the current source tree.
        subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--outdir",
                str(dist_dir),
                str(REPO_ROOT),
            ],
            check=True,
            capture_output=True,
        )
        wheels = list(dist_dir.glob("activegraph-*.whl"))
        assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
        wheel = wheels[0]

        # 2. Create a fresh venv. `with_pip=True` so we can install
        # into it. The venv is isolated from the test runner's
        # interpreter — no source-tree leakage.
        venv.create(venv_dir, with_pip=True, clear=True, symlinks=True)
        bin_dir = "Scripts" if os.name == "nt" else "bin"
        venv_python = venv_dir / bin_dir / "python"
        venv_activegraph = venv_dir / bin_dir / "activegraph"

        # 3. Install the wheel. No editable mode, no `[dev]` extras
        # — the wheel is what PyPI users get.
        subprocess.run(
            [str(venv_python), "-m", "pip", "install", "--quiet", str(wheel)],
            check=True,
            capture_output=True,
        )

        # 4. Smoke: `activegraph quickstart`. The default, non-
        # interactive, fixture-backed shape. Exits 0 on success;
        # raises (and the subprocess returns non-zero) on missing
        # runtime data.
        result = subprocess.run(
            [str(venv_activegraph), "quickstart"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            pytest.fail(
                "wheel-completeness gate failed: `activegraph quickstart` "
                f"against the installed wheel exited {result.returncode}.\n"
                f"--- stdout ---\n{result.stdout}\n"
                f"--- stderr ---\n{result.stderr}"
            )

        manifest_check = subprocess.run(
            [
                str(venv_python),
                "-c",
                (
                    "from pathlib import Path; "
                    "import activegraph.packs.diligence as d; "
                    "from activegraph.packs.manifest import "
                    "load_manifest, verify_content_hash, verify_surface; "
                    "root = Path(d.__file__).resolve().parent; "
                    "path = root / 'manifest.toml'; "
                    "assert path.is_file(), path; "
                    "manifest = load_manifest(path); "
                    "verify_surface(manifest, d.pack); "
                    "verify_content_hash(manifest, root)"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert manifest_check.returncode == 0, (
            "installed Diligence manifest verification failed:\n"
            f"stdout: {manifest_check.stdout}\n"
            f"stderr: {manifest_check.stderr}"
        )
