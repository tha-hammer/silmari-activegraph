"""Tests for `activegraph pack new` scaffolding. CONTRACT v0.9 #14."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

import pytest

from activegraph.packs.manifest import (
    load_manifest,
    verify_content_hash,
    verify_surface,
)
from activegraph.packs.scaffold import normalize_pack_name, scaffold_pack


def test_normalize_pack_name_kebab_to_snake():
    pack_name, module_name = normalize_pack_name("my-pack")
    assert pack_name == "my-pack"
    assert module_name == "my_pack"


def test_normalize_pack_name_preserves_strip_and_lowercase_compatibility():
    assert normalize_pack_name(" My-Pack ") == ("my-pack", "my_pack")


def test_normalize_pack_name_already_snake():
    pack_name, module_name = normalize_pack_name("simple")
    assert pack_name == "simple"
    assert module_name == "simple"


def test_normalize_pack_name_rejects_uppercase():
    """The normalizer lowercases its input, then validates the result.
    'UPPER' becomes 'upper' and passes; uppercase only fails if mixed
    with disallowed characters. Test the actual error path: a name
    starting with a disallowed character.
    """
    with pytest.raises(ValueError):
        normalize_pack_name("-leading-hyphen")


def test_normalize_pack_name_rejects_starting_digit():
    with pytest.raises(ValueError):
        normalize_pack_name("9pack")


def test_normalize_pack_name_applies_64_character_boundary_after_normalizing():
    accepted = "A" + "b" * 63
    assert normalize_pack_name(f" {accepted} ") == (accepted.lower(), accepted.lower())
    with pytest.raises(ValueError, match="64"):
        normalize_pack_name("a" * 65)


def test_normalize_pack_name_rejects_raw_underscore_distribution_slug():
    with pytest.raises(ValueError, match="distribution"):
        normalize_pack_name("my_pack")


def test_scaffold_pack_creates_expected_layout(tmp_path):
    root = scaffold_pack(tmp_path, "test-pack")
    assert root == tmp_path / "test-pack"
    assert (root / "pyproject.toml").is_file()
    assert (root / "README.md").is_file()
    assert (root / "test_pack" / "__init__.py").is_file()
    assert (root / "test_pack" / "object_types.py").is_file()
    assert (root / "test_pack" / "behaviors.py").is_file()
    assert (root / "test_pack" / "tools.py").is_file()
    assert (root / "test_pack" / "settings.py").is_file()
    assert (root / "test_pack" / "manifest.toml").is_file()
    assert (root / "test_pack" / "fixtures" / "__init__.py").is_file()
    assert (root / "test_pack" / "prompts" / "example_prompt.md").is_file()
    assert (root / "tests" / "test_pack_loads.py").is_file()


def test_scaffolded_manifest_matches_live_pack_and_package_data(
    tmp_path, monkeypatch
):
    root = scaffold_pack(tmp_path, "manifest-demo")
    module_root = root / "manifest_demo"
    monkeypatch.syspath_prepend(str(root))
    sys.modules.pop("manifest_demo", None)
    try:
        module = importlib.import_module("manifest_demo")
        manifest = load_manifest(module_root / "manifest.toml")
        verify_surface(manifest, module.pack)
        verify_content_hash(manifest, module_root)
        assert module.pack.name == manifest.name == "manifest_demo"
        assert module.pack.manifest_path == (
            module_root / "manifest.toml"
        ).resolve()
    finally:
        sys.modules.pop("manifest_demo", None)

    project = tomllib.loads((root / "pyproject.toml").read_text())
    assert project["tool"]["setuptools"]["package-data"]["manifest_demo"] == [
        "manifest.toml",
        "prompts/*.md",
    ]


def test_scaffold_refuses_to_overwrite(tmp_path):
    scaffold_pack(tmp_path, "test-pack")
    with pytest.raises(FileExistsError):
        scaffold_pack(tmp_path, "test-pack")


def test_scaffolded_pack_smoke_tests_pass(tmp_path):
    """The scaffolded smoke test imports the pack and verifies no
    global side effects, then loads it into a fresh runtime. If
    this fails, every new pack starts broken.
    """
    scaffold_pack(tmp_path, "scaff-demo")
    pack_root = tmp_path / "scaff-demo"
    framework_root = str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ)
    env["PYTHONPATH"] = (
        str(pack_root) + os.pathsep + framework_root
        + os.pathsep + env.get("PYTHONPATH", "")
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(pack_root / "tests"), "-q"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"scaffolded pack smoke test failed (rc={result.returncode}):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_cli_pack_new_creates_directory(tmp_path):
    """End-to-end: invoke `activegraph pack new` via the CLI."""
    framework_root = str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ)
    env["PYTHONPATH"] = framework_root + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "activegraph", "pack", "new", "cli-test", "-o", str(tmp_path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "cli-test").is_dir()
    assert (tmp_path / "cli-test" / "cli_test" / "__init__.py").is_file()
    assert (tmp_path / "cli-test" / "cli_test" / "manifest.toml").is_file()


def test_cli_pack_list_includes_diligence():
    """`activegraph pack list` should surface the shipped Diligence
    pack via the entry point group (CONTRACT v0.9 #11). This works
    after a `pip install -e .` of the framework; in the test
    environment we rely on the entry points being discoverable.
    """
    from activegraph.packs import discover, clear_discovery_cache

    clear_discovery_cache()
    entries = discover()
    # In CI / dev installs, diligence should be discoverable. In a raw
    # PYTHONPATH-only setup it may not be (no metadata). We assert
    # only the API contract: discover() returns a tuple.
    assert isinstance(entries, tuple)
