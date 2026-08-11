"""Recorded fixtures for the Diligence pack.

CONTRACT v0.9 #18: fixtures ship inside the pack package, not in the
framework and not in the user's tests directory. The killer demo
references them by relative path so the demo runs without API keys
and without network access.

Three companies, each with a small set of documents and canned
research findings. The `RecordedDiligenceProvider` is the
`LLMProvider`-protocol-conforming scripted provider used by the demo
and by the integration test.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from activegraph.llm.types import LLMMessage, LLMResponse

from activegraph.packs.diligence.fixtures.companies import (
    THREE_COMPANIES,
    company_goal,
    DOCS_BY_COMPANY,
    FILINGS_BY_COMPANY,
    SUMMARIES_BY_URL,
    QUESTIONS_BY_COMPANY,
    RESEARCH_FINDINGS_BY_QUESTION,
    RISKS_BY_COMPANY,
    MEMO_BODIES_BY_COMPANY,
)


# ---------------------------------------------------- tool fixtures


def lookup_company_docs(company_name: str, *, max_results: int = 5) -> list[dict]:
    docs = DOCS_BY_COMPANY.get(company_name.lower(), [])
    return docs[:max_results]


def lookup_filings(company_name: str, keyword: str = "") -> list[dict]:
    out = FILINGS_BY_COMPANY.get(company_name.lower(), [])
    if keyword:
        out = [f for f in out if keyword.lower() in (f["title"] + f.get("summary", "")).lower()]
    return out


def lookup_summary(url: str) -> dict:
    return SUMMARIES_BY_URL.get(url, {"summary": "", "facts": []})


# ---------------------------------------------------- scripted LLM provider


class RecordedDiligenceProvider:
    """Scripted LLM provider keyed off behavior name and the triggering
    object. Implements `LLMProvider` per CONTRACT v0.6 #3.

    The provider inspects:
      - the system prompt (extracts behavior_name from the
        'behavior named "..."' pattern)
      - the user message (splits on '## Triggering event' marker to
        isolate the triggering event payload)
      - the output_schema (selects which response shape to return)

    For the document_researcher, the provider returns:
      - turn 1: tool_call to `diligence.fetch_company_docs`
      - turn 2: tool_call to `diligence.summarize_document`
      - turn 3: the final ResearchFindings

    For all other LLM behaviors, returns the canned structured output
    in one turn.
    """

    def __init__(self, companies: list[dict]) -> None:
        # Used only for sanity; the dispatch logic keys off behavior name.
        self.companies = list(companies)

    def complete(
        self,
        *,
        system,
        messages,
        model,
        max_tokens,
        temperature,
        top_p,
        output_schema,
        timeout_seconds,
        tools=None,
    ) -> LLMResponse:
        behavior = _extract_behavior_name(system or "")
        last_user = _last_user_content(messages)
        company_name = _extract_company_name(last_user)

        if behavior == "diligence.question_generator":
            return _resp(model, output_schema, {
                "questions": QUESTIONS_BY_COMPANY.get(company_name.lower(), [
                    "What is the company's primary revenue source?",
                    "Who are the main competitors?",
                    "What is the growth trajectory?",
                    "What regulatory exposures exist?",
                    "How concentrated is the customer base?",
                    "What is the unit economics picture?",
                    "Are there any pending lawsuits?",
                    "What is the leadership team's track record?",
                ]),
            })

        if behavior == "diligence.document_researcher":
            return _researcher_turn(
                model=model,
                output_schema=output_schema,
                messages=messages,
                last_user=last_user,
                company_name=company_name,
            )

        if behavior == "diligence.risk_identifier":
            return _resp(model, output_schema, {
                "risks": RISKS_BY_COMPANY.get(company_name.lower(), [
                    {
                        "title": "limited fixture coverage",
                        "description": "Recorded fixtures do not include a risk profile for this company.",
                        "severity": "low",
                        "related_claim_texts": [],
                    },
                ]),
            })

        if behavior == "diligence.memo_synthesizer":
            return _resp(model, output_schema, MEMO_BODIES_BY_COMPANY.get(
                company_name.lower(),
                _empty_memo(company_name),
            ))

        # Fallback: empty schema-valid object.
        return _resp(model, output_schema, {})

    def estimate_cost(self, *, input_tokens, output_tokens, model) -> Decimal:
        return Decimal("0.001")

    def count_tokens(self, *, system, messages, model) -> int:
        total = len(system or "") + sum(len(m.content) for m in messages)
        return max(1, total // 4)


# ---------------------------------------------------- helpers


def _extract_behavior_name(system: str) -> str:
    """The runtime's system prompt opens with
    'You are an active-graph behavior named "<name>".' (per
    `activegraph.llm.prompt.build_system_prompt`). We sniff that back
    out, lowercased.
    """
    import re
    m = re.search(r'behavior named "([^"]+)"', system or "")
    if m:
        return m.group(1).lower()
    return ""


def _last_user_content(messages: list[LLMMessage]) -> str:
    for m in reversed(messages):
        if m.role == "user":
            return m.content
    return ""


def _extract_company_name(text: str) -> str:
    """Find which company this turn is about. Scope to the triggering
    event section so we don't pick up a different company that
    appears in the graph view block.
    """
    marker = "## Triggering event"
    if marker in text:
        section = text.split(marker, 1)[1]
        section = section.split("\n## ", 1)[0]
    else:
        section = text
    lower = section.lower()
    for c in THREE_COMPANIES:
        if c["name"].lower() in lower:
            return c["name"]
    # Fallback: scan the whole text. This is needed for the
    # question_generator turn, whose triggering event is `company.created`
    # — the company name is in the event payload's data.
    full_lower = text.lower()
    for c in THREE_COMPANIES:
        if c["name"].lower() in full_lower:
            return c["name"]
    return ""


def _researcher_turn(
    *, model, output_schema, messages, last_user, company_name: str,
) -> LLMResponse:
    """Three-turn loop:
      turn 1: tool_call fetch_company_docs
      turn 2: tool_call summarize_document
      turn 3: final ResearchFindings
    """
    from activegraph.llm.types import ToolCall

    fetched = any(
        m.role == "tool" and m.tool_name and "fetch_company_docs" in m.tool_name
        for m in messages
    )
    summarized = any(
        m.role == "tool" and m.tool_name and "summarize_document" in m.tool_name
        for m in messages
    )

    if not fetched:
        call = ToolCall(
            id="call_fetch",
            name="diligence.fetch_company_docs",
            args={"company_name": company_name, "max_results": 3},
        )
        return LLMResponse(
            raw_text="",
            parsed=None,
            input_tokens=100,
            output_tokens=15,
            cost_usd=Decimal("0.0008"),
            latency_seconds=0.3,
            model=model,
            finish_reason="tool_use",
            tool_calls=[call],
        )

    if not summarized:
        # Pull the first doc URL from the tool result.
        first_doc_url = _first_doc_url_from_history(messages, company_name)
        if first_doc_url is None:
            # Skip directly to final answer if fixtures didn't yield a doc.
            return _final_research(model, output_schema, last_user, company_name, doc_url="")
        call = ToolCall(
            id="call_summarize",
            name="diligence.summarize_document",
            args={"url": first_doc_url, "max_words": 80},
        )
        return LLMResponse(
            raw_text="",
            parsed=None,
            input_tokens=120,
            output_tokens=20,
            cost_usd=Decimal("0.0009"),
            latency_seconds=0.3,
            model=model,
            finish_reason="tool_use",
            tool_calls=[call],
        )

    # Final turn: build the ResearchFindings from the question + company.
    return _final_research(model, output_schema, last_user, company_name,
                           doc_url=_first_doc_url_from_history(messages, company_name) or "")


def _first_doc_url_from_history(messages: list[LLMMessage], company_name: str) -> Optional[str]:
    import json as _json
    for m in messages:
        if m.role != "tool":
            continue
        try:
            payload = _json.loads(m.content)
        except Exception:
            continue
        docs = payload.get("documents") or payload.get("filings") or []
        if docs:
            return docs[0].get("url")
    # Fallback to the first fixture doc for the company.
    docs = DOCS_BY_COMPANY.get(company_name.lower(), [])
    return docs[0]["url"] if docs else None


def _final_research(model, output_schema, last_user: str, company_name: str, doc_url: str) -> LLMResponse:
    question_text = _extract_question_text(last_user)
    findings = RESEARCH_FINDINGS_BY_QUESTION.get(
        (company_name.lower(), question_text),
        {
            "document_url": doc_url,
            "summary": (
                f"No fixture-specific findings for question {question_text!r}; "
                f"returning a generic placeholder."
            ),
            "claims": [
                {
                    "text": f"{company_name}: no specific finding for this question in the fixtures.",
                    "confidence": 0.3,
                    "source_document_url": doc_url,
                    "evidence_quote": "(no fixture quote)",
                },
            ],
        },
    )
    # Make sure document_url is set even when fixture omits it.
    if not findings.get("document_url"):
        findings = {**findings, "document_url": doc_url}
    return _resp(model, output_schema, findings)


def _extract_question_text(user_text: str) -> str:
    """The runtime's user message contains a '## Triggering event'
    section with the question's payload (per `build_user_message`).
    Scope extraction to that section so we don't pick up unrelated
    question objects from the view block.
    """
    import re
    marker = "## Triggering event"
    if marker in user_text:
        section = user_text.split(marker, 1)[1]
    else:
        section = user_text
    # Stop at the next ## section header so we only see triggering-event content.
    section = section.split("\n## ", 1)[0]
    m = re.search(r'"text"\s*:\s*"([^"]+)"', section)
    if m:
        return m.group(1)
    m = re.search(r"'text'\s*:\s*'([^']+)'", section)
    if m:
        return m.group(1)
    return ""


def _resp(model: str, output_schema, payload: dict) -> LLMResponse:
    import json as _json
    raw = _json.dumps(payload, sort_keys=True)
    parsed = output_schema.model_validate(payload) if output_schema else None
    return LLMResponse(
        raw_text=raw,
        parsed=parsed,
        input_tokens=110,
        output_tokens=22,
        cost_usd=Decimal("0.0010"),
        latency_seconds=0.4,
        model=model,
        finish_reason="end_turn",
    )


def _empty_memo(company_name: str) -> dict:
    return {
        "summary": f"Insufficient fixture data for {company_name}.",
        "thesis_questions_addressed": [],
        "key_claims": [],
        "open_contradictions": [],
        "contradictions_note": "no contradictions found",
        "risks": [
            {"risk_id": "", "title": "fixture coverage", "severity": "low",
             "description": "Limited fixture data."}
        ],
    }


__all__ = [
    "RecordedDiligenceProvider",
    "THREE_COMPANIES",
    "company_goal",
    "lookup_company_docs",
    "lookup_filings",
    "lookup_summary",
]
