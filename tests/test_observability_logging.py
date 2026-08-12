"""Structured logging — CONTRACT v0.8 #6–#7, #16."""

from __future__ import annotations

import copy
import io
import json
import logging

import pytest

from activegraph.observability.logging import (
    LOG_FIELDS,
    configure_logging,
    get_logger,
    runtime_log_extra,
)


@pytest.fixture
def captured_stream():
    stream = io.StringIO()
    configure_logging(level="DEBUG", json_output=True, stream=stream)
    yield stream
    # Reset to non-JSON default so other tests aren't affected
    logging.getLogger("activegraph").handlers.clear()


class TestLogSchema:
    @pytest.mark.parametrize(
        "use_helper", [True, False], ids=["helper", "stdlib-extra"]
    )
    def test_explicit_payload_is_detached_and_redacted(self, use_helper):
        stream = io.StringIO()
        original = {
            "public": "kept",
            "nested": {"secret": "do-not-log", "items": ["unchanged"]},
        }
        original_snapshot = copy.deepcopy(original)
        callback_inputs = []

        def redact(detached):
            callback_inputs.append(detached)
            detached["nested"]["secret"] = "[REDACTED]"
            detached["nested"]["items"].append("callback-only")
            return detached

        configure_logging(
            level="INFO",
            json_output=True,
            stream=stream,
            payload_redactor=redact,
        )
        log = get_logger("activegraph.test")
        extra = (
            runtime_log_extra(payload=original)
            if use_helper
            else {"payload": original}
        )
        log.info("explicit payload", extra=extra)

        lines = stream.getvalue().splitlines()
        assert len(callback_inputs) == 1
        assert callback_inputs[0] is not original
        assert callback_inputs[0]["nested"] is not original["nested"]
        assert len(lines) == 1
        decoded = json.loads(lines[0])
        assert decoded["payload"] == {
            "public": "kept",
            "nested": {
                "secret": "[REDACTED]",
                "items": ["unchanged", "callback-only"],
            },
        }
        assert "do-not-log" not in lines[0]
        assert original == original_snapshot

    def test_every_line_is_valid_json(self, captured_stream):
        log = get_logger("activegraph.test")
        log.info("hello", extra=runtime_log_extra(run_id="run_x"))
        log.warning("uh oh", extra=runtime_log_extra(run_id="run_x", behavior="b1"))
        lines = [l for l in captured_stream.getvalue().splitlines() if l.strip()]
        for ln in lines:
            obj = json.loads(ln)
            assert isinstance(obj, dict)

    def test_required_fields_always_present(self, captured_stream):
        log = get_logger("activegraph.test")
        log.info("hello")
        lines = [l for l in captured_stream.getvalue().splitlines() if l.strip()]
        obj = json.loads(lines[0])
        assert obj["level"] == "INFO"
        assert obj["logger"] == "activegraph.test"
        assert obj["message"] == "hello"
        assert "timestamp" in obj

    def test_optional_fields_omitted_when_absent(self, captured_stream):
        log = get_logger("activegraph.test")
        log.info("hello")
        obj = json.loads(captured_stream.getvalue().splitlines()[0])
        for k in ("run_id", "event_id", "behavior", "tool", "model"):
            assert k not in obj, f"{k} should be omitted, got {obj!r}"

    def test_documented_fields_pass_through(self, captured_stream):
        log = get_logger("activegraph.test")
        log.info(
            "behavior fired",
            extra=runtime_log_extra(
                run_id="run_x",
                event_id="evt_1",
                behavior="planner",
                latency_seconds=0.012,
                cost_usd="0.0042",
                cache_hit=False,
            ),
        )
        obj = json.loads(captured_stream.getvalue().splitlines()[0])
        assert obj["run_id"] == "run_x"
        assert obj["event_id"] == "evt_1"
        assert obj["behavior"] == "planner"
        assert obj["latency_seconds"] == 0.012
        assert obj["cost_usd"] == "0.0042"
        assert obj["cache_hit"] is False

    def test_undocumented_fields_dropped(self, captured_stream):
        log = get_logger("activegraph.test")
        log.info("x", extra=runtime_log_extra(run_id="r", custom_unknown="value"))
        obj = json.loads(captured_stream.getvalue().splitlines()[0])
        assert obj["run_id"] == "r"
        assert "custom_unknown" not in obj

    def test_log_fields_schema_snapshot(self):
        """The schema is the contract. Don't change LOG_FIELDS without
        bumping the framework's documented version. Add fields at the
        end of the tuple."""
        assert LOG_FIELDS == (
            "timestamp",
            "level",
            "logger",
            "message",
            "run_id",
            "event_id",
            "behavior",
            "tool",
            "model",
            "cache_hit",
            "cost_usd",
            "latency_seconds",
            "reason",
            "error_type",
            "error_message",
            # v1.0.3 #3 addition.
            "doc_url",
            # v1.11 addition.
            "payload",
        )


class TestConfigureLogging:
    def test_idempotent(self):
        """Repeated calls replace, not stack, handlers."""
        s1 = io.StringIO()
        configure_logging(level="INFO", stream=s1)
        configure_logging(level="INFO", stream=s1)
        configure_logging(level="INFO", stream=s1)
        handlers = [
            h
            for h in logging.getLogger("activegraph").handlers
            if getattr(h, "_activegraph", False)
        ]
        assert len(handlers) == 1
        logging.getLogger("activegraph").handlers.clear()

    def test_no_handlers_by_default(self):
        """Importing activegraph must not auto-configure logging."""
        logging.getLogger("activegraph").handlers.clear()
        import importlib

        import activegraph

        importlib.reload(activegraph)
        # After reload, no activegraph handlers should be installed.
        handlers = [
            h
            for h in logging.getLogger("activegraph").handlers
            if getattr(h, "_activegraph", False)
        ]
        assert handlers == []
