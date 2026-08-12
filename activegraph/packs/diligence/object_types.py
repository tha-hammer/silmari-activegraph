"""Diligence pack object and relation types. CONTRACT v0.9 #15.

Eight object types, seven relation types. The schemas are intentionally
small and concrete — packs that try to be everything tend to be
nothing. Future fields land here when there's a real consumer.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from activegraph.packs import ObjectType, RelationType


# ---------------------------------------------------- Pydantic schemas


class Company(BaseModel):
    """The target of a diligence run."""

    name: str
    ticker: Optional[str] = None
    sector: Optional[str] = None
    description: str = ""


class Document(BaseModel):
    """A source document the researcher pulled in."""

    title: str
    url: str
    company_id: str
    summary: str = ""
    published_at: Optional[str] = None  # ISO date string


class Question(BaseModel):
    """A research question generated from the thesis."""

    text: str
    company_id: str
    company_name: str = ""
    status: str = Field(default="open", pattern=r"^(open|answered|skipped)$")


class Claim(BaseModel):
    """A factual statement about the company, derived from a document."""

    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    company_id: str
    source_document_id: Optional[str] = None
    status: str = Field(default="open", pattern=r"^(open|reviewed|retracted)$")


class Evidence(BaseModel):
    """A verbatim quote (or excerpt) supporting a claim."""

    text: str
    document_id: str
    claim_id: str
    location: str = ""  # page number, section, anchor — free-text


class Contradiction(BaseModel):
    """A detected conflict between two claims.

    Created by the contradiction_detector pattern subscription. The
    pack does not auto-resolve in v0.9 (CONTRACT v0.9 #17); the memo
    synthesizer surfaces these as open questions.
    """

    claim_a_id: str
    claim_b_id: str
    rationale: str = ""
    status: str = Field(default="open", pattern=r"^(open|resolved)$")


class Risk(BaseModel):
    """A material risk identified during diligence."""

    title: str
    description: str
    severity: str = Field(default="medium", pattern=r"^(low|medium|high)$")
    company_id: str
    related_claim_ids: list[str] = []


class Memo(BaseModel):
    """The final diligence memo for a company.

    Structure is contracted (CONTRACT v0.9 #19): every memo has the
    same five sections and the test asserts the shape. Content quality
    is bounded by the fixtures / model; the structure is testable.
    """

    company_id: str
    summary: str
    thesis_questions_addressed: list[dict]  # [{question, status, claim_ids}]
    key_claims: list[dict]                  # [{claim_id, text, evidence_ids}]
    open_contradictions: list[dict]
    contradictions_note: str = ""           # "no contradictions found" when empty
    risks: list[dict]


# ---------------------------------------------------- ObjectType list


OBJECT_TYPES = [
    ObjectType(name="company", schema=Company, description="A target company."),
    ObjectType(name="document", schema=Document, description="A source document."),
    ObjectType(name="question", schema=Question, description="A research question."),
    ObjectType(name="claim", schema=Claim, description="A claim about the company."),
    ObjectType(name="evidence", schema=Evidence, description="A quote supporting a claim."),
    ObjectType(name="contradiction", schema=Contradiction, description="A detected conflict."),
    ObjectType(name="risk", schema=Risk, description="An identified material risk."),
    ObjectType(name="memo", schema=Memo, description="The final diligence memo."),
]


# ---------------------------------------------------- RelationType list


RELATION_TYPES = [
    RelationType(
        name="addresses",
        source_types=("claim",),
        target_types=("question",),
        description="A claim addresses a research question.",
    ),
    RelationType(
        name="supports",
        source_types=("evidence",),
        target_types=("claim",),
        description="Evidence supports a claim.",
    ),
    RelationType(
        name="contradicts",
        source_types=("claim",),
        target_types=("claim",),
        description="Two claims are in conflict.",
    ),
    RelationType(
        name="has_contradiction",
        source_types=("claim",),
        target_types=("contradiction",),
        description="A claim participates in a contradiction review item.",
    ),
    RelationType(
        name="references",
        source_types=("claim", "memo"),
        target_types=("document",),
        description="A claim or memo references a source document.",
    ),
    RelationType(
        name="derived_from",
        source_types=("claim", "evidence"),
        target_types=("document",),
        description="A claim or evidence was derived from a source document.",
    ),
    RelationType(
        name="mitigates",
        source_types=("evidence", "claim"),
        target_types=("risk",),
        description="Evidence or a claim mitigates a risk.",
    ),
]
