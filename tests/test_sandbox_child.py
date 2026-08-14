"""06.4 + 06.5: `_resolve_scenario` containment + sys.modules registration.

`_child.py` internals have no direct unit test today; all existing
coverage goes through a real subprocess in `test_sandbox_trial.py`. These
tests import `activegraph.sandbox._child` directly and exercise
`_resolve_scenario` in-process — cheap, deterministic, and precise about
exactly what's being asserted (no import/exec happened at all, not just
"the returned callable was discarded").
"""

from __future__ import annotations

import sys

import pytest

from activegraph.sandbox import _child


def test_resolve_scenario_rejects_path_outside_root(tmp_path):
    root = tmp_path / "pack"
    root.mkdir()
    (root / "__init__.py").write_text("")

    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    evil = outside / "evil.py"
    evil.write_text(
        f"from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('imported')\n"
        f"\n"
        f"def main(rt):\n"
        f"    pass\n"
    )

    with pytest.raises(RuntimeError) as excinfo:
        _child._resolve_scenario(root, "../outside/evil.py::main")

    assert "escapes" in str(excinfo.value) or "outside" in str(excinfo.value)
    assert not sentinel.exists(), (
        "the escaping module must never be imported/executed, not just "
        "have its returned callable discarded"
    )


def test_resolve_scenario_still_resolves_valid_in_root_scenario(tmp_path):
    root = tmp_path / "pack"
    root.mkdir()
    (root / "__init__.py").write_text("")
    (root / "scenario.py").write_text(
        "def main(rt):\n"
        "    return 'ran'\n"
    )

    try:
        fn = _child._resolve_scenario(root, "scenario.py::main")
        assert callable(fn)
        assert fn(None) == "ran"
    finally:
        sys.modules.pop("_trial_scenario", None)


def test_resolve_scenario_registers_module_in_sys_modules(tmp_path):
    root = tmp_path / "pack"
    root.mkdir()
    (root / "__init__.py").write_text("")
    (root / "scenario.py").write_text(
        "def main(rt):\n"
        "    pass\n"
    )

    try:
        fn = _child._resolve_scenario(root, "scenario.py::main")
        assert "_trial_scenario" in sys.modules
        assert sys.modules["_trial_scenario"].main is fn
    finally:
        sys.modules.pop("_trial_scenario", None)
