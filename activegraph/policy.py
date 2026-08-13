"""Per-behavior policy metadata.

The fields are recorded for audit and future hardening; they do not intercept
graph mutations.  In particular, ``requires_approval`` does not turn direct
``Graph.add_object`` calls into pending approvals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Policy:
    """Declared write/tool allowlists for a behavior.

    ``behavior`` names the behavior the lists scope to; the ``can_*``
    fields enumerate the object types, relation types, patches, and
    tool names it may touch. ``requires_approval`` is declarative
    metadata only: behavior code must explicitly call
    ``Context.propose_object`` to create a pending approval. Pack-level
    ``PackPolicy.requires_approval`` separately supplies owner attribution
    for such explicit proposals; neither surface intercepts direct graph
    writes. Broader enforcement is reserved for a hardening pass.
    """

    behavior: Optional[str] = None
    can_create: list[str] = field(default_factory=list)
    can_create_relation: list[str] = field(default_factory=list)
    can_propose: list[str] = field(default_factory=list)
    can_apply: list[str] = field(default_factory=list)
    can_call_tool: list[str] = field(default_factory=list)
    requires_approval: list[str] = field(default_factory=list)
