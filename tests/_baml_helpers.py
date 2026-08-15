"""Shared View/Event/Frame fixture for BAML render-prompt comparisons.

``tests/test_llm_prompt.py`` has no pytest fixtures for View/Event/Frame --
only two plain, importable helper functions (``_populated_view``,
``_bare_event``) and one inline, non-reusable ``Frame(...)`` construction.
This module builds the single real fixture shared between
``tests/test_llm_prompt.py``-style assertions and BAML render-prompt
comparisons (Behavior 4), from those two existing functions plus a fresh
minimal ``Frame``.
"""

from __future__ import annotations

from activegraph import Frame, Graph

from tests.test_llm_prompt import _bare_event, _populated_view


def shared_fixture_kwargs() -> dict:
    g = Graph()
    return dict(
        view=_populated_view(g),
        event=_bare_event(g),
        frame=Frame(goal="Audit Q3", constraints=["Be concise"]),
    )
