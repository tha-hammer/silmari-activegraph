"""06.1: conformance modules don't hard-require pytest at module scope.

``activegraph/store/graph_conformance.py`` (492 lines) has zero
``import pytest`` and zero exception-path assertions, and its own module
docstring already claims to work "under any test runner." The other three
conformance modules — ``sandbox/conformance.py``, ``store/conformance.py``,
``sinks/conformance.py`` — hard-required ``pytest`` at module scope solely
to use ``pytest.raises(...)`` inside one or two test methods each, directly
contradicting that same portability claim.

Uses ``ast.parse`` + walking ``Import``/``ImportFrom`` nodes rather than a
naive substring grep, to avoid false positives on ``pytest`` appearing in a
comment or docstring (which all three files legitimately do, describing
the very hard-dependency this test guards against).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

CONFORMANCE_MODULES = [
    "activegraph/sandbox/conformance.py",
    "activegraph/store/conformance.py",
    "activegraph/sinks/conformance.py",
]


def _imports_pytest_at_module_scope(path: Path) -> bool:
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "pytest" for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module == "pytest":
                return True
    return False


@pytest.mark.parametrize("relative_path", CONFORMANCE_MODULES)
def test_conformance_module_does_not_import_pytest(relative_path):
    path = REPO_ROOT / relative_path
    assert not _imports_pytest_at_module_scope(path), (
        f"{relative_path} imports pytest at module scope — conformance "
        f"modules are meant to work under any test runner, matching "
        f"store/conformance.py's own docstring claim and "
        f"store/graph_conformance.py's reference pattern (zero pytest "
        f"dependency)."
    )
