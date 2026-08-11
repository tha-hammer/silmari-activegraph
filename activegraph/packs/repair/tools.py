"""Repair pack tools — read/patch/check the real activegraph source tree.

Direct-edit mode: `apply_patch` writes to real files in the working
tree. Nothing is committed by any tool here — git tracks every change
for review and revert.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from activegraph.packs import tool

# activegraph/packs/repair/tools.py -> parents[3] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]


class ReadSourceInput(BaseModel):
    path: str
    start_line: Optional[int] = None
    end_line: Optional[int] = None


class ReadSourceOutput(BaseModel):
    content: str


@tool(
    name="read_source",
    description=(
        "Read a file from the repo (optionally a 1-indexed inclusive line "
        "range) to confirm its CURRENT exact content before patching."
    ),
    input_schema=ReadSourceInput,
    output_schema=ReadSourceOutput,
    cost_per_call=Decimal("0"),
    timeout_seconds=5.0,
    deterministic=False,
)
def read_source(args: ReadSourceInput, ctx) -> ReadSourceOutput:
    full_path = REPO_ROOT / args.path
    lines = full_path.read_text().splitlines(keepends=True)
    start = (args.start_line - 1) if args.start_line else 0
    end = args.end_line if args.end_line else len(lines)
    start = max(0, start)
    end = min(len(lines), end)
    return ReadSourceOutput(content="".join(lines[start:end]))


class ApplyPatchInput(BaseModel):
    path: str
    old_string: str
    new_string: str


class ApplyPatchOutput(BaseModel):
    ok: bool
    message: str


@tool(
    name="apply_patch",
    description=(
        "Replace old_string with new_string in the given file. old_string "
        "must appear exactly once — call read_source first and copy its "
        "exact text, not a paraphrase. Writes directly to the real file."
    ),
    input_schema=ApplyPatchInput,
    output_schema=ApplyPatchOutput,
    cost_per_call=Decimal("0"),
    timeout_seconds=5.0,
    deterministic=False,
)
def apply_patch(args: ApplyPatchInput, ctx) -> ApplyPatchOutput:
    full_path = REPO_ROOT / args.path
    text = full_path.read_text()
    count = text.count(args.old_string)
    if count == 0:
        return ApplyPatchOutput(
            ok=False,
            message="old_string not found in file — call read_source to get exact current text",
        )
    if count > 1:
        return ApplyPatchOutput(
            ok=False,
            message=f"old_string is not unique ({count} occurrences) — include more surrounding context",
        )
    full_path.write_text(text.replace(args.old_string, args.new_string, 1))
    return ApplyPatchOutput(ok=True, message="patch applied")


class RunCheckInput(BaseModel):
    path: str


class RunCheckOutput(BaseModel):
    ok: bool
    message: str


@tool(
    name="run_check",
    description=(
        "Parse the given file with Python's ast module to confirm it is "
        "still syntactically valid after a patch."
    ),
    input_schema=RunCheckInput,
    output_schema=RunCheckOutput,
    cost_per_call=Decimal("0"),
    timeout_seconds=5.0,
    deterministic=False,
)
def run_check(args: RunCheckInput, ctx) -> RunCheckOutput:
    full_path = REPO_ROOT / args.path
    try:
        ast.parse(full_path.read_text(), filename=str(full_path))
        return RunCheckOutput(ok=True, message="syntax OK")
    except SyntaxError as e:
        return RunCheckOutput(ok=False, message=f"{type(e).__name__}: {e}")


TOOLS = [read_source, apply_patch, run_check]
