"""`activegraph pack new <name>` scaffolding. CONTRACT v0.9 #14.

Generates a runnable Python package layout that:
  - declares activegraph as a dependency
  - registers itself under the activegraph.packs entry point
  - has stubs for object types, behaviors, tools, settings
  - has a smoke test that imports the pack and verifies no global
    registry side effects, then loads it into a fresh runtime
"""

from __future__ import annotations

import re
from pathlib import Path

from activegraph.packs.validation import validate_pack_name


_DISTRIBUTION_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


def normalize_pack_name(raw: str) -> tuple[str, str]:
    """Return `(directory_name, python_module_name)`.

    - kebab → snake for the module name (Python package directories
      can be kebab-case for distribution but the import name is the
      snake form, per packaging conventions).
    - lowercases and validates.
    """
    name = raw.strip().lower()
    if _DISTRIBUTION_SLUG_RE.fullmatch(name) is None:
        raise ValueError(
            f"distribution slug {raw!r} must match "
            f"[a-z][a-z0-9-]{{0,63}} (1–64 lowercase ASCII characters)"
        )
    module_name = name.replace("-", "_")
    validate_pack_name(module_name, field="normalized Pack.name")
    return name, module_name


def scaffold_pack(target_dir: Path, raw_name: str) -> Path:
    """Generate the pack at `target_dir / pack_name`. Returns the
    created path. Raises FileExistsError if the directory already
    exists.
    """
    pack_name, module_name = normalize_pack_name(raw_name)
    root = target_dir / pack_name
    if root.exists():
        raise FileExistsError(f"{root} already exists")
    root.mkdir(parents=True)
    (root / module_name).mkdir()
    (root / module_name / "prompts").mkdir()
    (root / "tests").mkdir()

    files = {
        root / "pyproject.toml": _PYPROJECT_TEMPLATE.format(
            pack_name=pack_name, module_name=module_name
        ),
        root / "README.md": _README_TEMPLATE.format(pack_name=pack_name, module_name=module_name),
        root / module_name / "__init__.py": _render_init(pack_name, module_name),
        root / module_name / "object_types.py": _OBJECT_TYPES_TEMPLATE,
        root / module_name / "behaviors.py": _BEHAVIORS_TEMPLATE.format(
            module_name=module_name
        ),
        root / module_name / "tools.py": _TOOLS_TEMPLATE,
        root / module_name / "settings.py": _SETTINGS_TEMPLATE.format(
            pack_name_title=_title(module_name)
        ),
        root / module_name / "prompts" / "example_prompt.md": _PROMPT_TEMPLATE,
        root / "tests" / "test_pack_loads.py": _SMOKE_TEST_TEMPLATE.format(
            module_name=module_name, pack_name=pack_name
        ),
    }
    for path, content in files.items():
        path.write_text(content, encoding="utf-8")
    return root


# ---------------------------------------------------- templates


_PYPROJECT_TEMPLATE = """\
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "{pack_name}"
version = "0.1.0"
description = "An activegraph pack."
requires-python = ">=3.11"
dependencies = ["activegraph>=0.9", "pydantic>=2"]

[project.entry-points."activegraph.packs"]
{pack_name} = "{module_name}:pack"

[tool.setuptools.packages.find]
include = ["{module_name}*"]
"""


_README_TEMPLATE = """\
# {pack_name}

An [activegraph](https://github.com/yoheinakajima/activegraph) pack.

## Install

```sh
pip install -e .
```

## Use

```python
from activegraph import Runtime, Graph
from activegraph.packs import load_by_name

rt = Runtime(Graph(), llm_provider=...)
rt.load_pack(load_by_name("{pack_name}"))
rt.run_goal("...")
```

## Develop

```sh
pip install -e .[dev]
pytest
```

Pack authoring guide:
<https://github.com/yoheinakajima/activegraph/blob/main/docs/pack_authoring.md>
"""


_PACK_INIT_TEMPLATE = '''\
"""{module_name} — an activegraph pack."""

from __future__ import annotations

from pathlib import Path

from activegraph.packs import Pack, load_prompts_from_dir

from {module_name}.behaviors import BEHAVIORS
from {module_name}.object_types import OBJECT_TYPES, RELATION_TYPES
from {module_name}.settings import {pack_name_title}Settings
from {module_name}.tools import TOOLS


_PROMPTS_DIR = Path(__file__).parent / "prompts"


pack = Pack(
    name="{module_name}",
    version="0.1.0",
    description="An activegraph pack.",
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    prompts=load_prompts_from_dir(_PROMPTS_DIR),
    settings_schema={pack_name_title}Settings,
)


__all__ = ["pack", "{pack_name_title}Settings"]
'''


def _title(name: str) -> str:
    return "".join(p.capitalize() for p in name.split("_"))


# Patch the template generation to insert the titled class name
def _render_init(pack_name: str, module_name: str) -> str:
    return _PACK_INIT_TEMPLATE.format(
        pack_name=pack_name,
        module_name=module_name,
        pack_name_title=_title(module_name),
    )


_OBJECT_TYPES_TEMPLATE = '''\
"""Object types and relation types declared by this pack."""

from __future__ import annotations

from pydantic import BaseModel

from activegraph.packs import ObjectType, RelationType


class Item(BaseModel):
    name: str
    notes: str = ""


OBJECT_TYPES = [
    ObjectType(name="item", schema=Item, description="An example item."),
]


RELATION_TYPES = [
    # RelationType(name="...", source_types=("item",), target_types=("item",)),
]
'''


_BEHAVIORS_TEMPLATE = '''\
"""Pack behaviors. Decorators imported from `activegraph.packs` so
they do NOT register globally (CONTRACT v0.9 #3)."""

from __future__ import annotations

from activegraph.packs import behavior


@behavior(name="hello", on=["goal.created"])
def hello(event, graph, ctx):
    """Example behavior. Replace with your own."""
    graph.add_object("item", {{"name": "hello world"}})


BEHAVIORS = [hello]
'''


_TOOLS_TEMPLATE = '''\
"""Pack-scoped tools. CONTRACT v0.9 #9.

Tools declared here are registered with the `{pack}.{name}` canonical
form when the pack loads. Pass `export_globally=True` to ALSO register
the short form globally.
"""

from __future__ import annotations

from activegraph.packs import tool


# Add your tools here, e.g.:
# from pydantic import BaseModel
#
# class FooIn(BaseModel):
#     x: str
#
# class FooOut(BaseModel):
#     y: str
#
# @tool(name="foo", input_schema=FooIn, output_schema=FooOut)
# def foo(args: FooIn, ctx) -> FooOut:
#     return FooOut(y=args.x.upper())


TOOLS: list = []
'''


_SETTINGS_TEMPLATE = '''\
"""Settings model for the pack. Accessed by behaviors via typed
parameter injection or `ctx.settings`."""

from __future__ import annotations

from pydantic import BaseModel


class {pack_name_title}Settings(BaseModel):
    """Pack settings. Add fields as needed. All fields should have
    defaults so `runtime.load_pack(pack)` works without explicit
    `settings=`.
    """

    threshold: float = 0.5
'''


_PROMPT_TEMPLATE = """---
version = "1.0.0"
---
You are an example behavior. Replace this prompt with your own.

When wired to an `@llm_behavior` with the same `name=`, this body
becomes part of the behavior's system prompt. The runtime
auto-injects view and triggering-event blocks into the user message.

Content is hashed for replay determinism — if you edit this prompt,
the hash changes, even if you forget to bump the declared version.
"""


_SMOKE_TEST_TEMPLATE = '''\
"""Smoke test: the pack imports without side effects and loads cleanly."""

from __future__ import annotations

from activegraph import Graph, Runtime, clear_registry, clear_tool_registry, get_registry, get_tool_registry


def test_import_has_no_global_side_effects():
    """CONTRACT v0.9 #3: pack-aware decorators don't register globally."""
    clear_registry()
    clear_tool_registry()
    import {module_name}  # noqa: F401
    assert get_registry() == [], (
        "importing the pack registered behaviors globally — pack code must "
        "use activegraph.packs decorators, not activegraph decorators"
    )
    assert get_tool_registry() == [], (
        "importing the pack registered tools globally"
    )


def test_pack_loads_into_fresh_runtime():
    from {module_name} import pack
    rt = Runtime(Graph())
    rt.load_pack(pack)
    pack_loaded_events = [e for e in rt.graph.events if e.type == "pack.loaded"]
    assert len(pack_loaded_events) == 1
    assert pack_loaded_events[0].payload["name"] == "{module_name}"
'''
