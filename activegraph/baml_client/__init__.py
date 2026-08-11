"""Top-level import surface for the BAML-generated client.

Survives `baml generate` regeneration — only `baml_sdk/` is
regenerated/gitignored; this file is committed.
"""

from activegraph.baml_client import baml_sdk

__all__ = ["baml_sdk"]
