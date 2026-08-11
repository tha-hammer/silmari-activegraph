"""A deterministic local HTTP server for generated BAML client tests."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterable


@dataclass(frozen=True)
class _ScriptedResponse:
    status: int
    body: Any
    delay_seconds: float = 0.0
    reset_connection: bool = False


class MockLLMHTTPServer:
    """Thread-safe scripted HTTP server with prefix-route accounting."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._scripts: dict[str, list[_ScriptedResponse]] = {}
        self._positions: dict[str, int] = defaultdict(int)
        self._paths: list[str] = []

        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length:
                    self.rfile.read(content_length)
                response = fixture._next_response(self.path)
                if response.delay_seconds:
                    time.sleep(response.delay_seconds)
                if response.reset_connection:
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    self.connection.close()
                    return

                if isinstance(response.body, bytes):
                    payload = response.body
                elif isinstance(response.body, str):
                    payload = response.body.encode("utf-8")
                else:
                    payload = json.dumps(response.body).encode("utf-8")

                self.send_response(response.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="mock-llm-http-server",
            daemon=True,
        )
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def script_response(
        self,
        *,
        route: str = "/",
        status: int,
        body: Any,
        delay_seconds: float = 0.0,
    ) -> None:
        self._set_script(
            route,
            [_ScriptedResponse(status, body, delay_seconds=delay_seconds)],
        )

    def script_sequence(
        self,
        *,
        route: str = "/",
        statuses_then_body: Iterable[tuple[int, Any] | tuple[int, Any, float]],
    ) -> None:
        """Script responses in order, repeating the final one thereafter."""
        responses = [
            _ScriptedResponse(
                status=entry[0],
                body=entry[1],
                delay_seconds=entry[2] if len(entry) == 3 else 0.0,
            )
            for entry in statuses_then_body
        ]
        if not responses:
            raise ValueError("a scripted response sequence cannot be empty")
        self._set_script(route, responses)

    def script_connection_reset(self, *, route: str) -> None:
        self._set_script(
            route,
            [_ScriptedResponse(0, None, reset_connection=True)],
        )

    def request_count(self, route: str = "/") -> int:
        return self.hits(route)

    def hits(self, route: str) -> int:
        with self._lock:
            return sum(path.startswith(route) for path in self._paths)

    def request_paths(self) -> list[str]:
        with self._lock:
            return list(self._paths)

    def reset(self) -> None:
        with self._lock:
            self._scripts.clear()
            self._positions.clear()
            self._paths.clear()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def _set_script(self, route: str, responses: list[_ScriptedResponse]) -> None:
        if not route.startswith("/"):
            raise ValueError("scripted routes must start with '/'")
        with self._lock:
            self._scripts[route] = responses
            self._positions[route] = 0

    def _next_response(self, path: str) -> _ScriptedResponse:
        with self._lock:
            self._paths.append(path)
            matching = [route for route in self._scripts if path.startswith(route)]
            if not matching:
                return _ScriptedResponse(
                    500,
                    {"error": f"no response scripted for {path}"},
                )
            route = max(matching, key=len)
            responses = self._scripts[route]
            position = self._positions[route]
            self._positions[route] = position + 1
            return responses[min(position, len(responses) - 1)]
