"""v1.0.3 #3: WARNING log + Runtime.errors property for behavior.failed.

The architecture stays unchanged — failures are events, not exceptions
(CONTRACT v0.6 #11). v1.0.3 #3 adds two user-facing surfaces so a
caller running `runtime.run_goal()` doesn't have to inspect
`graph._events` to notice failures:

  1. WARNING log line emitted by `Runtime._emit_behavior_failed`.
     Every behavior.failed routes through this method, so exactly
     one log line is produced per failure at one consistent level.
  2. `Runtime.errors` property returning a list of `BehaviorFailure`
     NamedTuples projected from the graph's behavior.failed events.
"""

from __future__ import annotations

import io
import json
import logging

import pytest

from activegraph import (
    BehaviorFailure,
    Graph,
    ObjectNotFoundError,
    Runtime,
    behavior,
)
from activegraph.observability.logging import configure_logging


@pytest.fixture
def captured_json_log():
    """Attach a JSON-line handler to the activegraph logger and yield
    the in-memory stream. Cleanup detaches the handler so other tests
    keep their stdlib defaults."""
    stream = io.StringIO()
    configure_logging(level="DEBUG", json_output=True, stream=stream)
    yield stream
    logging.getLogger("activegraph").handlers.clear()


def _lines(stream: io.StringIO) -> list[dict]:
    return [
        json.loads(l)
        for l in stream.getvalue().splitlines()
        if l.strip()
    ]


def test_behavior_failure_emits_warning_log(captured_json_log):
    @behavior(name="boom", on=["goal.created"])
    def _boom(event, graph, ctx):
        raise ValueError("kaboom")

    rt = Runtime(Graph())
    rt.run_goal("trigger")

    lines = _lines(captured_json_log)
    failed = [l for l in lines if l.get("level") == "WARNING" and l.get("behavior") == "boom"]
    assert len(failed) == 1
    rec = failed[0]
    # The reason for a generic exception falls back to
    # `exception.<class>` so callers can branch on it.
    assert rec["reason"] == "exception.ValueError"
    assert rec["error_type"] == "ValueError"
    assert rec["error_message"] == "kaboom"
    # The More: URL points at the class-level doc-page (the v1.0 #4
    # More: URL convention). Generic exception failures land on the
    # execution-error page.
    assert rec["doc_url"].endswith("/errors/execution-error")
    assert "behavior failed: boom" in rec["message"]
    failed_event = next(e for e in rt.graph.events if e.type == "behavior.failed")
    assert "ValueError: kaboom" in failed_event.payload["traceback"]
    assert "payload" not in rec
    assert "traceback" not in rec


def test_object_lookup_failure_preserves_event_error_log_and_metric_shapes(
    captured_json_log,
):
    class RecordingMetrics:
        def __init__(self):
            self.counters = []

        def counter(self, name, tags, value=1.0):
            self.counters.append((name, dict(tags), value))

        def histogram(self, name, tags, value):
            pass

        def gauge(self, name, tags, value):
            pass

    @behavior(name="missing_target", on=["goal.created"])
    def _missing_target(event, graph, ctx):
        graph.patch_object("task#404", {"status": "closed"})

    metrics = RecordingMetrics()
    rt = Runtime(Graph(), metrics=metrics)
    rt.run_goal("trigger")

    expected_message = str(ObjectNotFoundError(object_id="task#404"))
    failures = [e for e in rt.graph.events if e.type == "behavior.failed"]
    assert len(failures) == 1
    payload = failures[0].payload
    assert payload["exception_type"] == "ObjectNotFoundError"
    assert payload["message"] == expected_message
    assert "reason" not in payload

    assert len(rt.errors) == 1
    assert rt.errors[0].exception_type == "ObjectNotFoundError"
    assert rt.errors[0].message == expected_message
    assert rt.errors[0].reason is None

    records = [
        record
        for record in _lines(captured_json_log)
        if record.get("behavior") == "missing_target"
        and record.get("level") == "WARNING"
    ]
    assert len(records) == 1
    record = records[0]
    assert record["reason"] == "exception.ObjectNotFoundError"
    assert record["error_type"] == "ObjectNotFoundError"
    assert record["error_message"] == expected_message
    assert record["doc_url"].endswith("/errors/execution-error")

    # CONTRACT v1.11 #7: the metric's reason tag is deliberately closed —
    # arbitrary exception class names bound to "exception.other" so
    # cardinality stays fixed. The log line and graph-event payload above
    # retain the exact "exception.ObjectNotFoundError" diagnostic.
    assert (
        "activegraph_behaviors_failed_total",
        {
            "behavior": "missing_target",
            "reason": "exception.other",
        },
        1.0,
    ) in metrics.counters


def test_behavior_failure_doc_url_uses_reason_prefix(captured_json_log):
    """LLM-reason failures point at the LLMBehaviorError doc page,
    not the generic execution-error page."""

    from activegraph.llm.errors import LLMBehaviorError

    @behavior(name="llm_boom", on=["goal.created"])
    def _boom(event, graph, ctx):
        raise LLMBehaviorError("llm.network_error", "upstream down")

    rt = Runtime(Graph())
    rt.run_goal("trigger")

    lines = _lines(captured_json_log)
    rec = next(
        l for l in lines
        if l.get("level") == "WARNING" and l.get("behavior") == "llm_boom"
    )
    # Note: this test exercises the `_doc_url_for_reason` mapping
    # directly. The behavior raised a raw LLMBehaviorError so the
    # runtime's exception catch routes it through _emit_behavior_failed
    # with `reason=None` and exception_type=LLMBehaviorError — i.e.,
    # `reason` here defaults to `exception.LLMBehaviorError`. We
    # therefore assert against the helper too so the URL path is
    # exercised end-to-end at the framework boundary.
    from activegraph.runtime.runtime import _doc_url_for_reason

    assert _doc_url_for_reason("llm.parse_error").endswith("/errors/llm-behavior-error")
    assert _doc_url_for_reason("llm.schema_violation").endswith("/errors/llm-behavior-error")
    assert _doc_url_for_reason("tool.unknown_tool").endswith("/errors/tool-error")
    assert _doc_url_for_reason("budget.cost_exhausted").endswith("/errors/budget-exhausted")
    assert _doc_url_for_reason("exception.RuntimeError").endswith("/errors/execution-error")
    # The log line itself still carries a doc_url, just the generic
    # execution-error one for the raw-exception path.
    assert rec["doc_url"].endswith("/errors/execution-error")


def test_runtime_errors_returns_structured_view():
    @behavior(name="boom", on=["goal.created"])
    def _boom(event, graph, ctx):
        raise ValueError("kaboom")

    @behavior(name="ok", on=["goal.created"])
    def _ok(event, graph, ctx):
        pass

    rt = Runtime(Graph())
    rt.run_goal("trigger")

    errs = rt.errors
    assert len(errs) == 1
    err = errs[0]
    assert isinstance(err, BehaviorFailure)
    assert err.behavior == "boom"
    assert err.exception_type == "ValueError"
    assert err.message == "kaboom"
    # reason is None for raw-exception failures (no v0.6 #11 code).
    assert err.reason is None
    # event_id ties back to the triggering event, failed_event_id ties
    # back to the behavior.failed event itself.
    triggering = next(e for e in rt.graph._events if e.type == "goal.created")
    failed = next(e for e in rt.graph._events if e.type == "behavior.failed")
    assert err.event_id == triggering.id
    assert err.failed_event_id == failed.id


def test_runtime_errors_is_empty_on_clean_run():
    @behavior(name="ok", on=["goal.created"])
    def _ok(event, graph, ctx):
        pass

    rt = Runtime(Graph())
    rt.run_goal("trigger")
    assert rt.errors == []


def test_runtime_errors_accumulates_multiple_failures():
    @behavior(name="first", on=["goal.created"])
    def _f(event, graph, ctx):
        raise ValueError("first")

    @behavior(name="second", on=["goal.created"])
    def _s(event, graph, ctx):
        raise RuntimeError("second")

    rt = Runtime(Graph())
    rt.run_goal("trigger")

    by_behavior = {e.behavior: e for e in rt.errors}
    assert set(by_behavior) == {"first", "second"}
    assert by_behavior["first"].exception_type == "ValueError"
    assert by_behavior["second"].exception_type == "RuntimeError"


def test_runtime_errors_carries_reason_when_set():
    """When a behavior raises LLMBehaviorError, the runtime's LLM
    invocation path forwards `reason` and `payload_extras` to the
    centralized emitter. The BehaviorFailure exposes that reason.
    """
    from activegraph.llm.errors import LLMBehaviorError

    @behavior(name="bad_llm", on=["goal.created"])
    def _bad(event, graph, ctx):
        # Raised from inside a regular @behavior — the runtime catches
        # via the generic handler and emits reason=None. We exercise
        # the LLM-path reason routing via the underlying emitter API.
        raise LLMBehaviorError("llm.network_error", "upstream down")

    rt = Runtime(Graph())
    rt._emit_behavior_failed(
        "manual",
        "evt_x",
        LLMBehaviorError("llm.rate_limited", "429"),
        reason="llm.rate_limited",
    )

    err = next(e for e in rt.errors if e.behavior == "manual")
    assert err.reason == "llm.rate_limited"


def test_only_one_log_line_per_failure(captured_json_log):
    """The function-behavior exception handler used to emit an ERROR
    log line alongside the lifecycle event. v1.0.3 #3 routes both
    through _emit_behavior_failed so exactly one WARNING fires per
    failure. Operators tailing logs see one line, not two."""

    @behavior(name="solo", on=["goal.created"])
    def _solo(event, graph, ctx):
        raise ValueError("x")

    rt = Runtime(Graph())
    rt.run_goal("trigger")

    lines = _lines(captured_json_log)
    matching = [l for l in lines if l.get("behavior") == "solo" and l.get("error_type") == "ValueError"]
    # One WARNING line for the failure. No ERROR duplicate.
    assert len(matching) == 1
    assert matching[0]["level"] == "WARNING"


def test_log_can_be_silenced_via_standard_logging_config(captured_json_log):
    """Users opt out via the stdlib logging API."""

    logging.getLogger("activegraph.runtime").setLevel(logging.CRITICAL)

    @behavior(name="silent", on=["goal.created"])
    def _silent(event, graph, ctx):
        raise ValueError("x")

    try:
        rt = Runtime(Graph())
        rt.run_goal("trigger")
        lines = _lines(captured_json_log)
        # No WARNING from the runtime logger; the event still emitted.
        assert not any(
            l.get("behavior") == "silent" and l.get("level") == "WARNING"
            for l in lines
        )
        # The event landed on the graph regardless of log level.
        assert any(e.type == "behavior.failed" for e in rt.graph._events)
        assert len(rt.errors) == 1
    finally:
        logging.getLogger("activegraph.runtime").setLevel(logging.NOTSET)


def test_relation_behavior_failures_also_route_through_central_emitter():
    """v1.0.3 #3 also reroutes the relation-behavior exception
    handler; previously it called _emit_lifecycle directly with no
    log line. After the fix, relation-behavior failures appear in
    runtime.errors and emit the same WARNING shape."""

    from activegraph import relation_behavior

    @relation_behavior("depends_on", on=["relation.created"])
    def _boom(relation, event, graph, ctx):
        raise ValueError("rel boom")

    g = Graph()
    rt = Runtime(g)
    a = g.add_object("task", {})
    b = g.add_object("task", {})
    g.add_relation(a.id, b.id, "depends_on")
    rt.run_until_idle()

    errs = rt.errors
    assert len(errs) == 1
    assert errs[0].behavior == "_boom"
    assert errs[0].exception_type == "ValueError"
