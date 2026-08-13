"""Loader-side manifest validation, warning tier (CONTRACT v1.6 #1).

When a ``manifest.toml`` sits at the pack root, ``load_pack`` runs
``load_manifest`` + ``verify_surface`` and WARNs on violations —
structured, once per pack per process, never an error before 2.0.
Absent manifest: silent. All offline and deterministic.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from activegraph import Graph, Pack, PackValidationError, RelationType, Runtime
from activegraph.packs import loader as pack_loader
from activegraph.packs.manifest import compute_content_hash

PACK_INIT_TEMPLATE = '''
from activegraph.packs import Pack, behavior
from activegraph.packs.manifest import CapabilityDecl


@behavior(name="greeter", on=["goal.created"])
def greeter(event, graph, ctx):
    graph.add_object("greeting", {{"text": "hi"}})


pack = Pack(
    name="{name}",
    version="0.1.0",
    behaviors=(greeter,),
    capabilities={capabilities},
)
'''

MANIFEST_TEMPLATE = """
[pack]
name = "{name}"
version = "0.1.0"
description = "Warning-tier fixture."
license = ""

[pack.provenance]
authored_by = "agent"
generator = "test"

[pack.integrity]
content_hash = "{content_hash}"

[dependencies]
activegraph = ">=1.5,<2.0"
python-deps = []

[surface]
object_types = []
relation_types = []
behaviors = [{behaviors}]
tools = []
settings_schema = ""

[fixtures]
entrypoint = "fixtures/run_fixtures.py"
deterministic = true
"""


@pytest.fixture(autouse=True)
def _fresh_tier():
    """Each test exercises the tier from scratch: the once-per-process
    dedupe set is process state, so tests reset it explicitly."""
    pack_loader._manifest_checked.clear()
    yield
    pack_loader._manifest_checked.clear()


def _make_pack(tmp_path, name, *, declared_behaviors='"greeter"',
               capabilities="()", surface_extra="", manifest=True,
               manifest_text=None):
    """Write a real pack package to disk, import it so its module has
    a resolvable ``__file__``, and return the live Pack."""
    root = tmp_path / name
    root.mkdir()
    (root / "__init__.py").write_text(
        PACK_INIT_TEMPLATE.format(name=name, capabilities=capabilities)
    )
    (root / "fixtures").mkdir()
    (root / "fixtures" / "run_fixtures.py").write_text("# deterministic\n")
    if manifest:
        if manifest_text is None:
            manifest_text = MANIFEST_TEMPLATE.format(
                name=name,
                content_hash=compute_content_hash(root),
                behaviors=declared_behaviors,
            )
            if surface_extra:
                manifest_text = manifest_text.replace(
                    "\n[fixtures]", f"\n{surface_extra}\n\n[fixtures]"
                )
        (root / "manifest.toml").write_text(manifest_text)
    spec = importlib.util.spec_from_file_location(
        name, root / "__init__.py", submodule_search_locations=[str(root)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module.pack


def _tier_records(caplog):
    return [
        r
        for r in caplog.records
        if r.name == "activegraph.packs.manifest"
        and r.levelno >= logging.WARNING
    ]


def test_matching_manifest_loads_silently(tmp_path, caplog):
    pack = _make_pack(tmp_path, "warn_tier_clean")
    with caplog.at_level(logging.WARNING, logger="activegraph.packs.manifest"):
        rt = Runtime(Graph(), behaviors=[])
        assert rt.load_pack(pack) is True
    assert _tier_records(caplog) == []


def test_absent_manifest_stays_silent(tmp_path, caplog):
    pack = _make_pack(tmp_path, "warn_tier_bare", manifest=False)
    with caplog.at_level(logging.WARNING, logger="activegraph.packs.manifest"):
        rt = Runtime(Graph(), behaviors=[])
        assert rt.load_pack(pack) is True
    assert _tier_records(caplog) == []


def test_surface_mismatch_warns_once_structured_and_still_loads(
    tmp_path, caplog
):
    # The manifest declares a behavior the Pack never registers: a
    # real violation. The pack STILL loads — warning tier only.
    pack = _make_pack(
        tmp_path, "warn_tier_drift", declared_behaviors='"greeter", "ghost"'
    )
    with caplog.at_level(logging.WARNING, logger="activegraph.packs.manifest"):
        rt = Runtime(Graph(), behaviors=[])
        assert rt.load_pack(pack) is True
        # Loaded despite the violation: the behavior dispatches.
        rt.run_goal("check")
        assert [
            o for o in rt.graph.all_objects() if o.type == "greeting"
        ]
        # A second runtime loading the same pack does not re-warn:
        # once per pack per process.
        rt2 = Runtime(Graph(), behaviors=[])
        assert rt2.load_pack(pack) is True

    records = _tier_records(caplog)
    assert len(records) == 1
    (record,) = records
    # Structured: the record carries the machine-readable fields.
    assert record.pack == "warn_tier_drift"
    assert record.pack_version == "0.1.0"
    assert record.reason == "pack.manifest_invalid"
    assert record.manifest_path.endswith("manifest.toml")
    assert any("ghost" in v for v in record.violations)
    # And the human line says the pack still loads.
    assert "still loads" in record.getMessage()


CAPABILITY_PACK_DECL = """(
    CapabilityDecl(
        provider="meeting",
        capability="export_summary",
        risk_class="low",
        action_class="R2",
    ),
)"""

CAPABILITY_MANIFEST_DECL = '''[[surface.capabilities]]
provider = "meeting"
capability = "export_summary"
risk_class = "medium"
action_class = "R2"'''


def test_capability_mismatch_warns_but_pack_loads_and_dispatches(
    tmp_path, caplog
):
    pack = _make_pack(
        tmp_path,
        "warn_tier_capability",
        capabilities=CAPABILITY_PACK_DECL,
        surface_extra=CAPABILITY_MANIFEST_DECL,
    )

    with caplog.at_level(logging.WARNING, logger="activegraph.packs.manifest"):
        rt = Runtime(Graph(), behaviors=[])
        assert rt.load_pack(pack) is True
        rt.run_goal("capability warning remains advisory")

    assert [obj.type for obj in rt.graph.all_objects()] == ["greeting"]
    records = _tier_records(caplog)
    assert len(records) == 1
    assert records[0].reason == "pack.manifest_invalid"
    assert any(
        "meeting.export_summary risk_class mismatch" in violation
        for violation in records[0].violations
    )


def test_consumes_only_manifest_difference_is_silent_and_pack_dispatches(
    tmp_path, caplog
):
    pack = _make_pack(
        tmp_path,
        "warn_tier_consumes",
        surface_extra='consumes = ["gateway.search"]',
    )

    with caplog.at_level(logging.WARNING, logger="activegraph.packs.manifest"):
        rt = Runtime(Graph(), behaviors=[])
        assert rt.load_pack(pack) is True
        rt.run_goal("consumes remains host-owned")

    assert [obj.type for obj in rt.graph.all_objects()] == ["greeting"]
    assert _tier_records(caplog) == []


def test_malformed_manifest_warns_never_raises(tmp_path, caplog):
    pack = _make_pack(
        tmp_path,
        "warn_tier_garbage",
        manifest_text="this is not toml [[[",
    )
    with caplog.at_level(logging.WARNING, logger="activegraph.packs.manifest"):
        rt = Runtime(Graph(), behaviors=[])
        assert rt.load_pack(pack) is True  # never an error before 2.0
    records = _tier_records(caplog)
    assert len(records) == 1
    assert records[0].violations  # the parse failure, verbatim


def test_pack_manifest_path_rejects_string_and_relative_path():
    with pytest.raises(PackValidationError, match="manifest_path"):
        Pack(name="bad_string_locator", version="0.1.0", manifest_path="x")
    with pytest.raises(PackValidationError, match="absolute"):
        Pack(
            name="bad_relative_locator",
            version="0.1.0",
            manifest_path=Path("manifest.toml"),
        )


def test_explicit_manifest_path_wins_without_fallback(tmp_path, caplog):
    name = "warn_explicit_wins"
    pack = _make_pack(tmp_path, name)
    root = tmp_path / name
    explicit = root / "declared.toml"
    explicit.write_text(
        MANIFEST_TEMPLATE.format(
            name=name,
            content_hash=compute_content_hash(root),
            behaviors='"greeter"',
        )
    )
    (root / "manifest.toml").write_text("not valid [[[ TOML")
    pack = replace(pack, manifest_path=explicit.resolve())

    with caplog.at_level(logging.WARNING, logger="activegraph.packs.manifest"):
        assert Runtime(Graph(), behaviors=[]).load_pack(pack) is True

    assert _tier_records(caplog) == []


def test_explicit_missing_manifest_warns_once_by_pack_identity(tmp_path, caplog):
    pack = _make_pack(tmp_path, "warn_explicit_missing", manifest=False)
    first = replace(pack, manifest_path=(tmp_path / "missing-one.toml").resolve())
    second = replace(pack, manifest_path=(tmp_path / "missing-two.toml").resolve())

    with caplog.at_level(logging.WARNING, logger="activegraph.packs.manifest"):
        assert Runtime(Graph(), behaviors=[]).load_pack(first) is True
        assert Runtime(Graph(), behaviors=[]).load_pack(second) is True

    records = _tier_records(caplog)
    assert len(records) == 1
    record = records[0]
    assert record.pack == pack.name
    assert record.pack_version == pack.version
    assert record.manifest_path.endswith("missing-one.toml")
    assert record.reason == "pack.manifest_check_failed"
    assert record.failure_kind == "missing"
    assert record.error_type == "PackManifestError"
    assert "cannot read manifest" in record.error
    assert record.exc_info is not None


def test_explicit_directory_manifest_is_unreadable_warning(tmp_path, caplog):
    pack = _make_pack(tmp_path, "warn_explicit_directory")
    pack = replace(pack, manifest_path=(tmp_path / pack.name).resolve())

    with caplog.at_level(logging.WARNING, logger="activegraph.packs.manifest"):
        assert Runtime(Graph(), behaviors=[]).load_pack(pack) is True

    (record,) = _tier_records(caplog)
    assert record.reason == "pack.manifest_check_failed"
    assert record.failure_kind == "unreadable"
    assert record.error_type == "IsADirectoryError"


def test_unexpected_checker_failure_warns_and_pack_dispatches(
    tmp_path, caplog, monkeypatch
):
    pack = _make_pack(tmp_path, "warn_checker_bug")

    def fail_check(*args, **kwargs):
        raise RuntimeError("checker exploded")

    monkeypatch.setattr(
        "activegraph.packs.manifest.verify_surface", fail_check
    )
    with caplog.at_level(logging.WARNING, logger="activegraph.packs.manifest"):
        runtime = Runtime(Graph(), behaviors=[])
        assert runtime.load_pack(pack) is True
        runtime.run_goal("still dispatches")

    assert [o for o in runtime.graph.all_objects() if o.type == "greeting"]
    (record,) = _tier_records(caplog)
    assert record.reason == "pack.manifest_check_failed"
    assert record.failure_kind == "unexpected"
    assert record.error_type == "RuntimeError"
    assert record.error == "checker exploded"
    assert record.exc_info is not None


def test_locator_failure_warns_with_unresolved_path(tmp_path, caplog, monkeypatch):
    pack = _make_pack(tmp_path, "warn_locator_bug")

    def fail_locator(pack):
        raise RuntimeError("locator exploded")

    monkeypatch.setattr(pack_loader, "_locate_pack_manifest", fail_locator)
    with caplog.at_level(logging.WARNING, logger="activegraph.packs.manifest"):
        assert Runtime(Graph(), behaviors=[]).load_pack(pack) is True
        assert Runtime(Graph(), behaviors=[]).load_pack(pack) is True

    (record,) = _tier_records(caplog)
    assert record.manifest_path == "<unresolved>"
    assert record.failure_kind == "unexpected"
    assert record.error == "locator exploded"


def test_locator_oserror_is_an_unexpected_bug(tmp_path, caplog, monkeypatch):
    pack = _make_pack(tmp_path, "warn_locator_oserror")

    def fail_locator(pack):
        raise FileNotFoundError("locator implementation bug")

    monkeypatch.setattr(pack_loader, "_locate_pack_manifest", fail_locator)
    with caplog.at_level(logging.WARNING, logger="activegraph.packs.manifest"):
        assert Runtime(Graph(), behaviors=[]).load_pack(pack) is True

    (record,) = _tier_records(caplog)
    assert record.manifest_path == "<unresolved>"
    assert record.failure_kind == "unexpected"


def test_relation_only_pack_uses_explicit_manifest_path(tmp_path, caplog):
    root = tmp_path / "relation_only"
    root.mkdir()
    (root / "fixtures").mkdir()
    (root / "fixtures" / "run_fixtures.py").write_text("# deterministic\n")
    manifest = root / "manifest.toml"
    manifest.write_text(
        MANIFEST_TEMPLATE.format(
            name="relation_only",
            content_hash="sha256:" + "0" * 64,
            behaviors="",
        ).replace("relation_types = []", 'relation_types = ["links"]')
    )
    pack = Pack(
        name="relation_only",
        version="0.1.0",
        relation_types=(RelationType(name="links"),),
        manifest_path=manifest.resolve(),
    )

    with caplog.at_level(logging.WARNING, logger="activegraph.packs.manifest"):
        assert Runtime(Graph(), behaviors=[]).load_pack(pack) is True

    assert _tier_records(caplog) == []
