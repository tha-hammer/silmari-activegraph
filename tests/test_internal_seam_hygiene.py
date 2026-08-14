"""01.10: cross-module private-attribute writes use one consistent
suppression comment.

``Graph._pack_object_validator``/``_pack_relation_validator`` are real
attributes ``Graph.__init__`` already declares — nothing is
attribute-*undefined* about writing them from outside the class, so
``# type: ignore[attr-defined]`` is the wrong suppression. The actual lint
concern is the private-name cross-module access, which is ``SLF001``'s
domain — the same suppression every other intentional cross-module seam
in this repo already carries.

Plain grep-driven check (read the file, regex-match, assert), not an
import-time check — consistent with the file-hygiene, not runtime-behavior,
nature of this finding.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTIVEGRAPH_DIR = REPO_ROOT / "activegraph"
CORE_GRAPH_PATH = ACTIVEGRAPH_DIR / "core" / "graph.py"

_ASSIGNMENT_RE = re.compile(r"_pack_(?:object|relation)_validator\s*=")


def _cross_module_write_sites():
    """Every line assigning ``_pack_object_validator``/
    ``_pack_relation_validator`` outside ``core/graph.py`` — i.e. every
    site that writes the attribute from *outside* the class that owns it."""
    sites = []
    for path in ACTIVEGRAPH_DIR.rglob("*.py"):
        if path == CORE_GRAPH_PATH:
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if _ASSIGNMENT_RE.search(line):
                sites.append((path, lineno, line))
    return sites


def test_pack_validator_cross_module_writes_use_noqa_slf001():
    sites = _cross_module_write_sites()
    assert sites, "expected at least the two packs/loader.py write sites"

    for path, lineno, line in sites:
        assert "# noqa: SLF001" in line, (
            f"{path.relative_to(REPO_ROOT)}:{lineno} writes a private Graph "
            f"attribute from outside the class without the '# noqa: SLF001' "
            f"suppression every other cross-module seam site uses: {line!r}"
        )
        assert "type: ignore[attr-defined]" not in line, (
            f"{path.relative_to(REPO_ROOT)}:{lineno} uses the wrong "
            f"suppression — '_pack_object_validator'/'_pack_relation_validator' "
            f"are real attributes Graph.__init__ already declares, so mypy has "
            f"nothing to complain about; the actual lint concern is the "
            f"private-name access, which is SLF001's domain: {line!r}"
        )
