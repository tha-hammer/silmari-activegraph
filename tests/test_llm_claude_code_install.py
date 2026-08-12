"""ClaudeCodeProvider installation-surface tests. CONTRACT v1.11 #1,
Behavior 2.

Proves the public/optional-dependency contract: `import activegraph`,
`import activegraph.llm`, and `ClaudeCodeProvider` construction/
inspection all work with `claude_agent_sdk`/`anyio` unavailable (the
base-install case); the first `complete()` call becomes one terminal
`LLMBehaviorError(reason="llm.request_error")` naming the install
command; and — since `claude-agent-sdk` genuinely IS installed in this
dev/test environment as a `[dev]` extra — the real bundled CLI
resolution, exact-version check, and executable verification all run
for real against the installed package, not a simulation.

Base-extra isolation is proven via a subprocess with a `sys.meta_path`
import blocker (not `pip uninstall`, which the review flagged as
destructive to the developer environment — a blocked import in an
isolated subprocess proves the same "importable without the extra"
contract without touching this environment's real installation).
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from activegraph.llm import ClaudeCodeProvider, LLMBehaviorError, LLMMessage


_BLOCK_IMPORT_HOOK = """
import sys

class _Blocker:
    def find_module(self, name, path=None):
        if name in ("claude_agent_sdk", "anyio", "sniffio"):
            return self
        return None

    def load_module(self, name):
        raise ImportError(f"{name} is blocked for this test")

sys.meta_path.insert(0, _Blocker())
"""


def _run_blocked(code: str) -> subprocess.CompletedProcess:
    full = _BLOCK_IMPORT_HOOK + "\n" + code
    return subprocess.run([sys.executable, "-c", full], capture_output=True, text=True)


def test_base_import_activegraph_succeeds_without_sdk():
    result = _run_blocked("import activegraph\nprint('OK')\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_base_import_activegraph_llm_succeeds_without_sdk():
    result = _run_blocked("import activegraph.llm\nprint('OK')\n")
    assert result.returncode == 0, result.stdout + result.stderr


def test_base_construct_claude_code_provider_succeeds_without_sdk():
    result = _run_blocked(
        "from activegraph.llm import ClaudeCodeProvider\n"
        "p = ClaudeCodeProvider()\n"
        "print('OK')\n"
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_base_recognizes_model_and_capabilities_work_without_sdk():
    result = _run_blocked(
        "from activegraph.llm import ClaudeCodeProvider, get_llm_provider_capabilities\n"
        "p = ClaudeCodeProvider()\n"
        "assert p.recognizes_model('claude-sonnet-4-5') is True\n"
        "assert p.default_model == 'claude-sonnet-4-5'\n"
        "caps = get_llm_provider_capabilities(p)\n"
        "assert caps.requires_generation_control_acknowledgement is True\n"
        "print('OK')\n"
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_base_count_tokens_works_without_sdk():
    # count_tokens is pure-local (never touches the SDK loader).
    result = _run_blocked(
        "from activegraph.llm import ClaudeCodeProvider\n"
        "p = ClaudeCodeProvider()\n"
        "n = p.count_tokens(system='hi', messages=[], model='claude-sonnet-4-5')\n"
        "assert n >= 1\n"
        "print('OK')\n"
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_first_complete_call_without_sdk_is_one_terminal_request_error_with_install_hint():
    result = _run_blocked(
        "from activegraph.llm import ClaudeCodeProvider, LLMBehaviorError, LLMMessage\n"
        "p = ClaudeCodeProvider(allow_unenforced_generation_controls=True)\n"
        "try:\n"
        "    p.complete(system='', messages=[LLMMessage(role='user', content='hi')],\n"
        "               model='claude-sonnet-4-5', max_tokens=1, temperature=0.0, top_p=1.0,\n"
        "               output_schema=None, timeout_seconds=1.0)\n"
        "    print('NO_RAISE')\n"
        "except LLMBehaviorError as e:\n"
        "    assert e.reason == 'llm.request_error', e.reason\n"
        "    assert 'activegraph[claude-code]' in str(e)\n"
        "    print('OK')\n"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


# ---- the extra IS installed here (a [dev] dependency) — verify for real ---


def test_extra_installed_resolves_exact_pinned_sdk_version():
    import importlib.metadata

    assert importlib.metadata.version("claude-agent-sdk") == "0.2.135"


def test_extra_installed_bundled_cli_resolves_exists_and_is_executable():
    import os
    from pathlib import Path

    import claude_agent_sdk as _sdk

    from activegraph.llm.claude_code import _load_sdk_bindings

    bindings = _load_sdk_bindings()
    assert bindings.cli_path
    assert Path(bindings.cli_path).is_file()
    assert os.access(bindings.cli_path, os.X_OK)
    # Resolved from inside the installed package, never a system PATH
    # fallback or caller override.
    assert str(Path(_sdk.__file__).parent / "_bundled") in bindings.cli_path


def test_wrong_injected_distribution_version_fails_before_any_query(monkeypatch):
    import importlib.metadata

    from activegraph.llm.claude_code import _load_sdk_bindings

    real_version = importlib.metadata.version

    def _fake_version(name):
        if name == "claude-agent-sdk":
            return "9.9.9"
        return real_version(name)

    monkeypatch.setattr(importlib.metadata, "version", _fake_version)
    with pytest.raises(LLMBehaviorError) as exc:
        _load_sdk_bindings()
    assert exc.value.reason == "llm.request_error"
    assert "9.9.9" in str(exc.value)


def test_missing_bundled_file_fails_before_any_query(monkeypatch, tmp_path):
    fake_pkg_dir = tmp_path / "fake_claude_agent_sdk"
    fake_pkg_dir.mkdir()
    (fake_pkg_dir / "__init__.py").write_text("")
    monkeypatch.setattr("claude_agent_sdk.__file__", str(fake_pkg_dir / "__init__.py"))

    from activegraph.llm.claude_code import _load_sdk_bindings

    with pytest.raises(LLMBehaviorError) as exc:
        _load_sdk_bindings()
    assert exc.value.reason == "llm.request_error"
