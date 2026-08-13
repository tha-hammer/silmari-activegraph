"""Static gates for the v1.11 standard-metric production contract."""

from __future__ import annotations

from pathlib import Path
import re

from activegraph.observability.metrics import METRIC_NAMES


ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "guides" / "operating-in-production.md"
OBSERVABILITY_SPEC = ROOT / "specs" / "10-observability-trace-cli.md"
BEHAVIOR_SPEC = ROOT / "specs" / "09-tools-behaviors.md"
CONTRACT = ROOT / "CONTRACT.md"
CHANGELOG = ROOT / "CHANGELOG.md"


def test_operator_guide_metric_table_matches_catalog_exactly() -> None:
    text = GUIDE.read_text()
    table = text.split("### Standard metrics", 1)[1].split(
        "### Cardinality rule", 1
    )[0]
    rows = re.findall(
        r"^\| `(?P<name>activegraph_[^`]+)`\s+\|\s+"
        r"(?P<kind>counter|histogram|gauge)\s+\|\s+"
        r"(?P<tags>[^|]+?)\s+\|$",
        table,
        flags=re.MULTILINE,
    )
    documented = {}
    for name, kind, raw_tags in rows:
        tags = () if raw_tags.strip() == "(none)" else tuple(
            re.findall(r"`([^`]+)`", raw_tags)
        )
        documented[name] = (kind, tags)

    expected = {spec.name: (spec.kind, spec.tags) for spec in METRIC_NAMES}
    assert documented == expected


def test_metric_docs_lock_ownership_normalization_and_no_ghost_rules() -> None:
    combined = " ".join(
        (GUIDE.read_text() + OBSERVABILITY_SPEC.read_text()).split()
    )
    for phrase in (
        "all 24",
        "last writer wins",
        "never a sum",
        "Runtime-owned",
        "direct mutation",
        "no immediate metric-freshness guarantee",
        "unknown_model",
        "unknown_tool",
        "unknown_reason",
        "exception.other",
        "no initial queue/budget gauge",
    ):
        assert phrase.lower() in combined.lower()

    stale = (
        "declared catalog, not a guaranteed emission set",
        "14 of the 24 declared standard metrics",
        "activegraph_tools_* metrics are declared but never emitted",
        "no such hooks were found",
    )
    assert not any(phrase in combined for phrase in stale)


def test_contract_and_changelog_append_complete_metric_overlay() -> None:
    contract = CONTRACT.read_text()
    heading = (
        "## v1.11 #7. Standard metrics are emitted from authoritative "
        "runtime seams"
    )
    assert contract.count(heading) == 1
    overlay = " ".join(contract.split(heading, 1)[1].split())
    for phrase in (
        "exact 24 existing `METRIC_NAMES`",
        "literal `cache_hit is True`",
        "Invalid tool input is post-request",
        "last writer wins; it is not a sum",
        "Direct mutation or replacement of public `Runtime.budget`",
        "no initial queue/budget gauge ghost",
        "does not change graph event payloads or ordering",
    ):
        assert phrase in overlay

    changelog = " ".join(CHANGELOG.read_text().split())
    assert "Complete standard metric emission" in changelog
    assert "All 24 existing `METRIC_NAMES`" in changelog
    assert "unchanged names and tag keys" in changelog


def test_tool_behavior_spec_uses_real_response_failure_shape() -> None:
    normalized = " ".join(BEHAVIOR_SPEC.read_text().split())
    assert "`tool.responded.payload.error` Mapping" in normalized
    assert "Invalid input occurs after `tool.requested`" in normalized
    assert "There is no `tool.failed` event" in normalized
    assert "declared but never emitted" not in normalized
