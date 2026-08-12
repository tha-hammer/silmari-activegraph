"""Diligence pack behaviors. CONTRACT v0.9 #15 / #16 / #17.

Seven behaviors. Two are pure-Python deterministic (evidence_linker,
contradiction_detector — the latter via pattern subscription). The
others are LLM-backed.

This file is the reference implementation of pack-aware behaviors:
  - decorators imported from `activegraph.packs` (no global side effects)
  - settings via typed parameter injection (`*, settings: DiligenceSettings`)
  - pack-scoped tool refs (`tools=[fetch_company_docs]`)
  - prompts loaded from `prompts/<name>.md` by matching name (the pack
    loader wires them up — see `loader._resolve_prompt_template`)
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from activegraph.packs import behavior, llm_behavior, relation_behavior
from activegraph.packs.diligence.object_types import (
    Claim,
    Company,
    Contradiction,
    Document,
    Evidence,
    Memo,
    Question,
    Risk,
)
from activegraph.packs.diligence.settings import DiligenceSettings
from activegraph.packs.diligence.tools import (
    fetch_company_docs,
    search_filings,
    summarize_document,
)


# ---------------------------------------------------- LLM output schemas


class QuestionList(BaseModel):
    questions: list[str] = Field(min_length=1)


class ResearcherClaim(BaseModel):
    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_document_url: Optional[str] = None
    evidence_quote: str = ""
    # The verbatim text of an existing claim this new claim
    # contradicts. The handler resolves text -> claim_id and emits a
    # `:contradicts` edge, which the contradiction_detector pattern
    # subscription picks up. Optional; most claims do not contradict.
    contradicts_claim_text: Optional[str] = None


class ResearchFindings(BaseModel):
    document_url: str
    summary: str
    claims: list[ResearcherClaim]


class RiskList(BaseModel):
    class _Risk(BaseModel):
        title: str
        description: str
        severity: str = Field(default="medium", pattern=r"^(low|medium|high)$")
        related_claim_texts: list[str] = []

    risks: list[_Risk] = []


class MemoBody(BaseModel):
    summary: str
    thesis_questions_addressed: list[dict]
    key_claims: list[dict]
    open_contradictions: list[dict]
    contradictions_note: str = ""
    risks: list[dict]


# ---------------------------------------------------- behaviors


@behavior(
    name="company_planner",
    on=["goal.created"],
)
def company_planner(event, graph, ctx):
    """Bootstrap: turn the goal into a `company` object so downstream
    LLM behaviors have something to react to.

    The goal payload carries the company name (the demo's
    `company_goal()` helper formats it as "Diligence: <Company Name>").
    """
    goal_text = event.payload.get("goal", "")
    if not goal_text.startswith("Diligence:"):
        return
    company_name = goal_text.split("Diligence:", 1)[1].strip()
    graph.add_object(
        "company",
        Company(
            name=company_name,
            description=f"Target company for diligence run: {company_name}",
        ).model_dump(),
    )


@llm_behavior(
    name="question_generator",
    on=["object.created"],
    where={"object.type": "company"},
    description="Generate the initial set of research questions from "
                "the diligence thesis. One-shot in v0.9 — produces "
                "between min_questions and max_questions questions; "
                "the researcher works through them in order.",
    output_schema=QuestionList,
    creates=["question"],
    deterministic=True,
)
def question_generator(event, graph, ctx, out, *, settings: DiligenceSettings):
    """Typed settings injection (CONTRACT v0.9 #7 Form 1)."""
    company_id = event.payload["object"]["id"]
    company_name = event.payload["object"]["data"].get("name", "")
    questions = list(out.questions)
    # Honour the settings bounds. Trim or warn-via-trace if the model
    # over/undershoots.
    if len(questions) > settings.max_questions:
        questions = questions[: settings.max_questions]
    # min_questions is a soft floor — we record what we got.
    for q_text in questions:
        graph.add_object(
            "question",
            Question(
                text=q_text,
                company_id=company_id,
                company_name=company_name,
                status="open",
            ).model_dump(),
        )


@llm_behavior(
    name="document_researcher",
    on=["object.created"],
    where={"object.type": "question"},
    description=(
        "Research one question by fetching documents about the company, "
        "summarizing them, and extracting claims with confidence and "
        "evidence quotes. Use fetch_company_docs first, then "
        "summarize_document for promising documents."
    ),
    output_schema=ResearchFindings,
    tools=[fetch_company_docs, summarize_document],
    creates=["document", "claim", "evidence"],
    deterministic=True,
    budget={"max_tool_calls": 6},
)
def document_researcher(
    event, graph, ctx, out, *, settings: DiligenceSettings,
):
    question_id = event.payload["object"]["id"]
    q_obj = graph.get_object(question_id)
    if q_obj is None:
        return  # question vanished — defensive

    company_id = q_obj.data.get("company_id")
    # Materialize one document object (the one the LLM cited) plus claims.
    doc_url = out.document_url
    existing_doc = next(
        (o for o in ctx.view.objects(type="document") if o.data.get("url") == doc_url),
        None,
    )
    if existing_doc is None:
        doc = graph.add_object(
            "document",
            Document(
                title=_title_from_url(doc_url),
                url=doc_url,
                company_id=company_id or "",
                summary=out.summary,
            ).model_dump(),
        )
        doc_id = doc.id
    else:
        doc_id = existing_doc.id

    capped = list(out.claims)[: settings.max_claims_per_document]
    for rc in capped:
        claim = graph.add_object(
            "claim",
            Claim(
                text=rc.text,
                confidence=rc.confidence,
                company_id=company_id or "",
                source_document_id=doc_id,
                status="open",
            ).model_dump(),
        )
        graph.add_relation(claim.id, question_id, "addresses")
        graph.add_relation(claim.id, doc_id, "derived_from")
        # Attach an evidence quote.
        if rc.evidence_quote:
            ev = graph.add_object(
                "evidence",
                Evidence(
                    text=rc.evidence_quote,
                    document_id=doc_id,
                    claim_id=claim.id,
                    location="",
                ).model_dump(),
            )
            graph.add_relation(ev.id, claim.id, "supports")
        # If the researcher flagged a contradiction with an existing
        # claim, materialize the `:contradicts` edge. The pattern
        # subscription (contradiction_detector) creates the
        # `contradiction` object asynchronously.
        if rc.contradicts_claim_text:
            target = _find_claim_by_text(ctx, rc.contradicts_claim_text, company_id)
            if target is not None:
                graph.add_relation(claim.id, target, "contradicts")

    # Mark the question answered.
    graph.patch_object(question_id, {"status": "answered"})


def _find_claim_by_text(ctx, text: str, company_id: Optional[str]) -> Optional[str]:
    for o in ctx.view.objects(type="claim"):
        if company_id and o.data.get("company_id") != company_id:
            continue
        if o.data.get("text") == text:
            return o.id
    return None


@behavior(
    name="evidence_linker",
    on=["object.created"],
    where={"object.type": "evidence"},
)
def evidence_linker(event, graph, ctx):
    """Deterministic: when an evidence is created, ensure it's linked
    to its claim via a `supports` edge (the researcher already does this
    above, but this behavior is a safety net for evidence objects added
    by other paths or by future packs).
    """
    e_data = event.payload["object"]["data"]
    claim_id = e_data.get("claim_id")
    e_id = event.payload["object"]["id"]
    if not claim_id:
        return
    if graph.get_object(claim_id) is None:
        return
    # Idempotent: don't add a duplicate edge.
    for r in ctx.view.relations(type="supports"):
        if r.source == e_id and r.target == claim_id:
            return
    graph.add_relation(e_id, claim_id, "supports")


@behavior(
    name="contradiction_detector",
    on=["relation.created"],
    where={"relation.type": "contradicts"},
    pattern=(
        "(c1:claim)-[r:contradicts]->(c2:claim) "
        "WHERE c1.confidence > 0.7 AND c2.confidence > 0.7"
    ),
)
def contradiction_detector(event, graph, ctx, *, settings: DiligenceSettings):
    """Pattern subscription: fires on every new `:contradicts` edge
    between two claims whose confidence exceeds the threshold. Creates
    a `contradiction` object. NO automatic resolution in v0.9
    (CONTRACT v0.9 #17).
    """
    for m in ctx.matches:
        c1_id = m["c1"]
        c2_id = m["c2"]
        c1 = graph.get_object(c1_id)
        c2 = graph.get_object(c2_id)
        if c1 is None or c2 is None:
            continue
        # Apply the configured threshold (the pattern hard-codes 0.7;
        # if settings.confidence_threshold_for_review is HIGHER, gate
        # additionally here).
        if min(c1.data.get("confidence", 0), c2.data.get("confidence", 0)) < settings.confidence_threshold_for_review:
            continue
        contradiction = graph.add_object(
            "contradiction",
            Contradiction(
                claim_a_id=c1_id,
                claim_b_id=c2_id,
                rationale=(
                    f"Both claims exceed the confidence threshold "
                    f"({settings.confidence_threshold_for_review}) and assert "
                    f"conflicting facts. v0.9 surfaces this for human review."
                ),
                status="open",
            ).model_dump(),
        )
        graph.add_relation(c1_id, contradiction.id, "has_contradiction")
        graph.add_relation(c2_id, contradiction.id, "has_contradiction")


@llm_behavior(
    name="risk_identifier",
    # Triggered on every claim creation. The handler is idempotent —
    # it only produces risks once per company (checks the graph for
    # an existing risk). Simpler than activate_after for v0.9; the
    # killer demo doesn't need delayed scheduling here.
    on=["object.created"],
    where={"object.type": "claim"},
    description=(
        "Identify material risks for the company based on accumulated "
        "claims. Outputs a list of risks with severity and "
        "related_claim_texts (short verbatim quotes that the post-LLM "
        "handler will resolve to claim ids)."
    ),
    output_schema=RiskList,
    creates=["risk"],
    deterministic=True,
)
def risk_identifier(event, graph, ctx, out, *, settings: DiligenceSettings):
    """Idempotent: produces one risk batch per company. Maps the LLM's
    `related_claim_texts` (verbatim quotes) back to claim ids via
    exact text match.

    Uses ctx.propose_object when auto_approve_risks=False
    (CONTRACT v0.9 #15 — risk_approval policy).
    """
    claim_data = event.payload["object"]["data"]
    company_id = claim_data.get("company_id")
    if not company_id:
        return
    # Idempotency: one risk batch per company. Check both materialized
    # risks AND pending risk approvals (the pack's risk_approval policy
    # gates writes; pending approvals are not yet visible in the view).
    for o in ctx.view.objects(type="risk"):
        if o.data.get("company_id") == company_id:
            return
    if ctx._runtime is not None:
        for pa in ctx._runtime.pending_approvals():
            if pa.object_type == "risk" and pa.data.get("company_id") == company_id:
                return

    # Build a {text -> claim_id} lookup for the company's claims.
    text_to_claim: dict[str, str] = {}
    for o in ctx.view.objects(type="claim"):
        if o.data.get("company_id") != company_id:
            continue
        text_to_claim[o.data.get("text", "")] = o.id

    for r in out.risks:
        related = [text_to_claim[t] for t in r.related_claim_texts if t in text_to_claim]
        risk_payload = Risk(
            title=r.title,
            description=r.description,
            severity=r.severity,
            company_id=company_id,
            related_claim_ids=related,
        ).model_dump()
        if settings.auto_approve_risks:
            graph.add_object("risk", risk_payload)
        else:
            ctx.propose_object(
                "risk", risk_payload,
                reason=f"risk_approval policy: {r.title}",
            )


@llm_behavior(
    name="memo_synthesizer",
    on=["object.created"],
    # Fires once per company once its first risk lands. (The risk
    # identifier runs after claims have accumulated; the first risk
    # is the signal that diligence is "ready to summarize.")
    where={"object.type": "risk"},
    description=(
        "Synthesize the final diligence memo for the company. The "
        "memo MUST have the contracted structure: summary, thesis "
        "questions addressed, key claims (with evidence citations), "
        "open contradictions, risks. Cite evidence for every claim. "
        "If no contradictions were found, say so explicitly."
    ),
    output_schema=MemoBody,
    creates=["memo"],
    deterministic=True,
)
def memo_synthesizer(event, graph, ctx, out, *, settings: DiligenceSettings):
    risk_id = event.payload["object"]["id"]
    risk_obj = graph.get_object(risk_id)
    if risk_obj is None:
        return
    company_id = risk_obj.data.get("company_id")

    # Idempotent: only one memo per company. The risk_identifier may
    # produce more than one risk object — we only synthesize on the
    # first one.
    existing = [
        o for o in ctx.view.objects(type="memo")
        if o.data.get("company_id") == company_id
    ]
    if existing:
        return

    payload = Memo(
        company_id=company_id or "",
        summary=out.summary,
        thesis_questions_addressed=out.thesis_questions_addressed,
        key_claims=out.key_claims,
        open_contradictions=out.open_contradictions,
        contradictions_note=out.contradictions_note,
        risks=out.risks,
    ).model_dump()

    if settings.auto_approve_memos:
        graph.add_object("memo", payload)
    else:
        ctx.propose_object(
            "memo", payload,
            reason=f"memo_approval policy: company {company_id}",
        )


# ---------------------------------------------------- behavior list


BEHAVIORS = [
    company_planner,
    question_generator,
    document_researcher,
    evidence_linker,
    contradiction_detector,
    risk_identifier,
    memo_synthesizer,
]


# ---------------------------------------------------- helpers


def _title_from_url(url: str) -> str:
    if "://" in url:
        url = url.split("://", 1)[1]
    parts = url.rstrip("/").split("/")
    slug = parts[-1] if parts else url
    return slug.replace("-", " ").replace("_", " ").title() or url
