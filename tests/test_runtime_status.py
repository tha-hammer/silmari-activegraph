"""runtime.status() shape & semantics — CONTRACT v0.8 #11."""

from __future__ import annotations

import dataclasses
import inspect
import os
from pathlib import Path
import tempfile

import pytest

import activegraph.observability.status as status_module
from activegraph import Graph, Runtime, behavior, clear_registry
from activegraph.observability.status import (
    BudgetSnapshot,
    EventSummary,
    RuntimeStatus,
    status_to_dict,
)


def _normalized(text: str) -> str:
    return " ".join(text.replace("`", "").lower().split())


def _guide_runtime_introspection() -> str:
    guide = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "guides"
        / "operating-in-production.md"
    ).read_text(encoding="utf-8")
    section = guide.split("## Runtime introspection", 1)[1]
    return section.split("\n## ", 1)[0]


def test_status_complexity_contract_is_consistent() -> None:
    surfaces = {
        "Runtime.status": inspect.getdoc(Runtime.status) or "",
        "status module": inspect.getdoc(status_module) or "",
        "operating guide": _guide_runtime_introspection(),
    }
    required = (
        "side-effect-free and in-memory",
        "o(n + b + min(n, recent))",
        "n materialized history events",
        "b registered behaviors",
        "recent bounds returned summaries, not history construction",
        "no store i/o or object/relation traversal",
    )
    forbidden = (
        "cheap to call",
        "calling it is cheap",
        "no event log scan",
        "tail-slice",
    )

    for label, surface in surfaces.items():
        normalized = _normalized(surface)
        for phrase in required:
            assert phrase in normalized, f"{label} is missing {phrase!r}"
        for phrase in forbidden:
            assert phrase not in normalized, f"{label} retains {phrase!r}"


def _register():
    clear_registry()

    @behavior(name="planner", on=["goal.created"])
    def planner(event, graph, ctx):
        graph.add_object("task", {"x": 1})
        graph.add_object("task", {"x": 2})


class TestStatusShape:
    def test_returns_runtime_status(self):
        _register()
        g = Graph()
        rt = Runtime(g)
        rt.run_goal("x")
        s = rt.status()
        assert isinstance(s, RuntimeStatus)

    def test_frozen_dataclass_rejects_mutation(self):
        _register()
        g = Graph()
        rt = Runtime(g)
        rt.run_goal("x")
        s = rt.status()
        with pytest.raises(dataclasses.FrozenInstanceError):
            s.run_id = "spoofed"  # type: ignore[misc]

    def test_recent_arg_controls_tail_length(self):
        _register()
        g = Graph()
        rt = Runtime(g)
        rt.run_goal("x")
        assert len(rt.status(recent=3).recent_events) == 3
        assert len(rt.status(recent=0).recent_events) == 0
        total = len(rt.graph.events)
        assert len(rt.status(recent=1000).recent_events) == total

    def test_recent_negative_raises(self):
        _register()
        g = Graph()
        rt = Runtime(g)
        rt.run_goal("x")
        with pytest.raises(ValueError):
            rt.status(recent=-1)

    def test_no_last_error_field(self):
        """CONTRACT v0.8 #6 (revised) — explicitly drop last_error."""
        fields = {f.name for f in dataclasses.fields(RuntimeStatus)}
        assert "last_error" not in fields
        assert "last_failure" not in fields


class TestStatusState:
    def test_idle_state_after_run_to_completion(self):
        _register()
        g = Graph()
        rt = Runtime(g)
        rt.run_goal("x")
        s = rt.status()
        assert s.state == "idle"

    def test_exhausted_state_on_budget(self):
        _register()
        g = Graph()
        rt = Runtime(g, budget={"max_behavior_calls": 0})
        rt.run_goal("x")
        s = rt.status()
        assert s.state == "exhausted"

    def test_stopped_state_pre_run(self):
        _register()
        g = Graph()
        rt = Runtime(g)
        s = rt.status()
        # No events yet → stopped
        assert s.state == "stopped"

    def test_state_survives_save_load_round_trip(self):
        """A loaded runtime sees the same state as the runtime that saved."""
        _register()
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        os.remove(path)
        try:
            g = Graph()
            rt = Runtime(g, persist_to=path, budget={"max_behavior_calls": 0})
            rt.run_goal("x")
            rt.save_state()
            in_proc_state = rt.status().state
            assert in_proc_state == "exhausted"

            _register()
            rt2 = Runtime.load(path, run_id=rt.run_id)
            assert rt2.status().state == in_proc_state
        finally:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(path + suffix)
                except FileNotFoundError:
                    pass


class TestStatusToDict:
    def test_serializable_json(self):
        import json

        _register()
        g = Graph()
        rt = Runtime(g)
        rt.run_goal("x")
        d = status_to_dict(rt.status(recent=5))
        # Must round-trip through JSON.
        json.dumps(d, default=str)
        assert d["run_id"] == rt.run_id
        assert d["state"] in ("idle", "exhausted", "stopped", "running")
        assert isinstance(d["recent_events"], list)
        assert isinstance(d["budget"], dict)

    def test_registered_behaviors_have_kind_and_subscriptions(self):
        _register()
        g = Graph()
        rt = Runtime(g)
        rt.run_goal("x")
        s = rt.status()
        names = {b.name for b in s.registered_behaviors}
        assert "planner" in names
        for b in s.registered_behaviors:
            if b.name == "planner":
                assert b.kind == "function"
                assert "goal.created" in b.subscribed_to
