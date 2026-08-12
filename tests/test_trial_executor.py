"""TrialExecutor interface, local adapter, conformance, and recording double."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from activegraph.sandbox import (
    LocalSubprocessTrialExecutor,
    PackSource,
    RecordingTrialExecutor,
    TrialArtifactReference,
    TrialBudgetUse,
    TrialEventLogReference,
    TrialFailureDetails,
    TrialIsolationGuarantees,
    TrialLimits,
    TrialResult,
    TrialSpecification,
)
from activegraph.sandbox.conformance import TrialExecutorConformance
from activegraph.store.sqlite import SQLiteEventStore
from tests.test_sandbox_trial import _candidate_dir, _parent_store


class TestLocalSubprocessTrialExecutor(TrialExecutorConformance):
    """Run the reusable adapter suite against the first-party default."""

    __test__ = True

    def setup_method(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        store, parent_run, tip, _ = _parent_store(root)
        candidate, bundle = _candidate_dir(root)
        self._specification = TrialSpecification(
            store_path=store,
            parent_run_id=parent_run,
            at_event=tip,
            pack_source=PackSource(
                root_dir=str(candidate), expected_bundle_hash=bundle
            ),
            scenario="scenario.py",
            limits=TrialLimits(wall_clock_seconds=60),
        )

    def teardown_method(self) -> None:
        self._temp.cleanup()

    def make_executor(self) -> LocalSubprocessTrialExecutor:
        return LocalSubprocessTrialExecutor()

    def make_serialized_specification(self) -> str:
        return self._specification.to_json()


def _recorded_result(isolation: TrialIsolationGuarantees) -> TrialResult:
    return TrialResult(
        status="scenario_failed",
        budget_use=TrialBudgetUse(
            events_appended=3,
            behavior_failures=1,
            limits=TrialLimits(max_events=10),
        ),
        artifacts=(
            TrialArtifactReference(
                name="summary", uri="memory://summary", media_type="text/plain"
            ),
        ),
        event_log=TrialEventLogReference(
            store_path="record.db", run_id="run_recorded"
        ),
        failure=TrialFailureDetails(
            kind="scenario_failed", message="fixture failure", exit_code=30
        ),
        isolation=isolation,
        detail="fixture failure",
        exit_code=30,
    )


def test_recording_executor_records_parsed_spec_and_returns_fixture() -> None:
    isolation = TrialIsolationGuarantees(
        process="none",
        filesystem="none",
        network="none",
        syscalls="none",
        environment="none",
        security_sandbox=False,
    )
    fixture = _recorded_result(isolation)
    executor = RecordingTrialExecutor(
        [fixture], isolation_guarantees=isolation
    )
    specification = TrialSpecification(
        store_path="record.db",
        parent_run_id="run_parent",
        at_event="evt_001",
        pack_source=PackSource(
            root_dir="/pinned/pack", expected_bundle_hash="sha256:" + "a" * 64
        ),
    )

    assert executor.execute(specification.to_json()) == fixture
    assert executor.specifications == [specification]
    assert executor.serialized_specifications == [specification.to_json()]
    with pytest.raises(RuntimeError, match="no result remaining"):
        executor.execute(specification.to_json())


def test_local_executor_declares_honest_non_security_isolation() -> None:
    guarantees = LocalSubprocessTrialExecutor().isolation_guarantees
    assert guarantees.process == "fresh_interpreter_subprocess"
    assert guarantees.filesystem == "shared_host_filesystem"
    assert guarantees.network == "unconfined"
    assert guarantees.syscalls == "unconfined"
    assert guarantees.security_sandbox is False


_VALID_PIN = "sha256:" + "a" * 64


def _wire_payload(schema_version=2):
    return {
        "schema_version": schema_version,
        "store_path": "trial.db",
        "parent_run_id": "run_parent",
        "at_event": "evt_001",
        "pack_source": {
            "root_dir": "/candidate",
            "expected_bundle_hash": _VALID_PIN,
            "manifest_required": True,
        },
        "scenario": "",
        "limits": {},
        "label": "trial",
        "extra_packs": [
            {
                "root_dir": "/extra",
                "expected_bundle_hash": _VALID_PIN,
                "manifest_required": True,
            }
        ],
    }


def test_pack_source_requires_bundle_hash() -> None:
    with pytest.raises(TypeError, match="expected_bundle_hash"):
        PackSource(root_dir="/candidate")


@pytest.mark.parametrize(
    "pin",
    [
        "",
        "sha256:" + "A" * 64,
        "sha256:" + "a" * 63,
        "sha256:" + "a" * 65,
        "md5:" + "a" * 64,
        "sha256:" + "g" * 64,
        None,
        True,
    ],
)
def test_pack_source_rejects_invalid_bundle_hash(pin) -> None:
    with pytest.raises(ValueError, match="expected_bundle_hash"):
        PackSource(root_dir="/candidate", expected_bundle_hash=pin)


@pytest.mark.parametrize("schema_version", [1, 2])
@pytest.mark.parametrize(
    ("container", "value", "expected_path"),
    [
        ("pack_source", None, "pack_source.expected_bundle_hash"),
        ("pack_source", "", "pack_source.expected_bundle_hash"),
        ("pack_source", "sha256:BAD", "pack_source.expected_bundle_hash"),
        ("extra_packs", None, "extra_packs[0].expected_bundle_hash"),
        ("extra_packs", "", "extra_packs[0].expected_bundle_hash"),
        ("extra_packs", 7, "extra_packs[0].expected_bundle_hash"),
    ],
)
def test_wire_versions_require_nested_bundle_hash(
    schema_version, container, value, expected_path
) -> None:
    payload = _wire_payload(schema_version)
    source = (
        payload["pack_source"]
        if container == "pack_source"
        else payload["extra_packs"][0]
    )
    if value is None:
        del source["expected_bundle_hash"]
    else:
        source["expected_bundle_hash"] = value

    with pytest.raises(ValueError) as excinfo:
        TrialSpecification.from_json(json.dumps(payload))

    assert expected_path in str(excinfo.value)


def test_pinned_v1_migrates_to_canonical_v2() -> None:
    specification = TrialSpecification.from_json(json.dumps(_wire_payload(1)))
    assert specification.schema_version == 2
    emitted = specification.to_json()
    assert json.loads(emitted)["schema_version"] == 2
    assert emitted == json.dumps(
        json.loads(emitted), sort_keys=True, separators=(",", ":")
    )


def test_pinned_v2_round_trips_canonically() -> None:
    serialized = json.dumps(
        _wire_payload(2), sort_keys=True, separators=(",", ":")
    )
    specification = TrialSpecification.from_json(serialized)
    emitted = specification.to_json()
    assert TrialSpecification.from_json(emitted) == specification
    assert TrialSpecification.from_json(emitted).to_json() == emitted


@pytest.mark.parametrize("schema_version", [1, True, 2.0, "2"])
def test_direct_specification_rejects_non_v2_schema(schema_version) -> None:
    with pytest.raises(ValueError, match="schema_version"):
        TrialSpecification(
            store_path="trial.db",
            parent_run_id="run_parent",
            at_event="evt_001",
            pack_source=PackSource(
                root_dir="/candidate", expected_bundle_hash=_VALID_PIN
            ),
            schema_version=schema_version,
        )


@pytest.mark.parametrize("schema_version", [True, 2.0, "2", 3])
def test_specification_rejects_unknown_schema_version(schema_version) -> None:
    payload = _wire_payload(schema_version)
    with pytest.raises(ValueError, match="schema_version"):
        TrialSpecification.from_json(json.dumps(payload))


def test_local_executor_rejects_unpinned_v1_before_fork(tmp_path, monkeypatch) -> None:
    store_path, parent_run_id, at_event, _ = _parent_store(tmp_path)
    payload = _wire_payload(1)
    payload["store_path"] = store_path
    payload["parent_run_id"] = parent_run_id
    payload["at_event"] = at_event
    payload["pack_source"]["expected_bundle_hash"] = ""
    run_ids_before = [
        run.run_id for run in SQLiteEventStore.list_runs(store_path)
    ]
    called = False

    def forbidden_fork(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("fork seam reached")

    monkeypatch.setattr(
        "activegraph.sandbox._run_forked_trial_local", forbidden_fork
    )

    with pytest.raises(ValueError) as excinfo:
        LocalSubprocessTrialExecutor().execute(json.dumps(payload))

    assert "pack_source.expected_bundle_hash" in str(excinfo.value)
    assert called is False
    assert [run.run_id for run in SQLiteEventStore.list_runs(store_path)] == (
        run_ids_before
    )
