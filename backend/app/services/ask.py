"""Compose a cited answer from retrieved evidence (Mode A)."""

from __future__ import annotations

import logging

from app.services.llm import get_llm
from app.services.prompts import ANSWER_SYSTEM_PROMPT
from app.services.retrieval import Evidence, retrieve
from app.services.risk import flags_for_names

logger = logging.getLogger(__name__)


def _chunk_block(chunks: list[dict]) -> str:
    lines: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        title = chunk.get("title") or "untitled"
        url = chunk.get("url") or ""
        date = chunk.get("published_at") or ""
        score = chunk.get("score")
        score_bit = f", score={score:.2f}" if isinstance(score, (int, float)) else ""
        body = (chunk.get("text") or "").strip().replace("\n", " ")
        if len(body) > 700:
            body = body[:700].rsplit(" ", 1)[0] + "…"
        lines.append(f"[{index}] {title} ({date}{score_bit})\n{url}\n{body}")
    return "\n\n".join(lines) if lines else "(none)"


def _graph_block(evidence: Evidence) -> str:
    parts: list[str] = []
    if evidence.entities:
        listed = ", ".join(
            f"{e.get('name')} ({e.get('label')}, {e.get('mentions')} mentions)"
            for e in evidence.entities[:18]
            if e.get("name")
        )
        parts.append("Entities in the retrieved chunks: " + listed)
    for case in evidence.cases[:10]:
        via = ", ".join(case.get("via") or [])
        drugs = ", ".join(case.get("drugs") or [])
        locs = ", ".join(case.get("locations") or [])
        people = ", ".join(case.get("persons") or [])
        parts.append(
            f"Case “{case.get('title')}” ({case.get('date') or 'undated'}). "
            f"Linked via: {via or 'retrieved chunk'}. "
            f"People: {people or '—'}. Drugs: {drugs or '—'}. Places: {locs or '—'}."
        )
    return "\n".join(parts) if parts else "(no cross-article links in the current hit set)"


def _extractive_fallback(query: str, evidence: Evidence) -> str:
    """Used when the LLM is unavailable so the product still returns something."""
    if not evidence.chunks:
        return (
            f"No supporting chunks were found for “{query}”. "
            "The graph may be empty, or the question may be outside the ingested corpus."
        )
    lead = evidence.chunks[0]
    people = [e["name"] for e in evidence.entities if e.get("label") == "Person"][:6]
    cases = [c.get("title") for c in evidence.cases[:4] if c.get("title")]
    bits = [
        f"Closest source: {lead.get('title') or lead.get('url')} [1].",
        (lead.get("text") or "")[:400],
    ]
    if people:
        bits.append("Named people in the hit set: " + ", ".join(people) + ".")
    if cases:
        bits.append("Related cases in the graph: " + "; ".join(cases) + ".")
    bits.append("This is an extractive fallback; the LLM did not compose a narrative.")
    return " ".join(bits)


def _user_prompt(query: str, evidence: Evidence) -> str:
    return (
        f"QUESTION: {query}\n\n"
        f"EVIDENCE:\n{_chunk_block(evidence.chunks)}\n\n"
        f"GRAPH CONNECTIONS:\n{_graph_block(evidence)}"
    )


async def answer_query(query: str, *, top_n: int = 10) -> dict:
    evidence = await retrieve(query, top_n=top_n)
    person_names = [
        e["name"] for e in evidence.entities if e.get("label") == "Person" and e.get("name")
    ]
    for case in evidence.cases:
        person_names.extend(case.get("persons") or [])

    flags: list[dict] = []
    if evidence.entities or evidence.cases:
        try:
            flags = await flags_for_names(person_names)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Risk overlay failed: %s", exc)

    llm = get_llm()

    if not evidence.chunks and not evidence.cases:
        answer = (
            f"No on-topic evidence was found in the knowledge graph for “{query}”. "
            "Public-source discovery will search, crawl and ingest reporting, then this "
            "question is answered again from the updated graph."
        )
    elif evidence.chunks and llm.configured:
        try:
            answer = await llm.complete(
                ANSWER_SYSTEM_PROMPT, _user_prompt(query, evidence), max_tokens=700
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Answer composition failed (%s); using extractive fallback", exc)
            answer = _extractive_fallback(query, evidence)
    elif evidence.chunks:
        answer = _extractive_fallback(query, evidence)
    else:
        # Entity walk found cases but no supporting paragraphs — still answer from the graph.
        if llm.configured:
            try:
                answer = await llm.complete(
                    ANSWER_SYSTEM_PROMPT, _user_prompt(query, evidence), max_tokens=700
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Answer composition failed (%s); using extractive fallback", exc)
                answer = _extractive_fallback(query, evidence)
        else:
            answer = _extractive_fallback(query, evidence)

    sources = []
    seen: set[str] = set()
    for index, chunk in enumerate(evidence.chunks, start=1):
        url = chunk.get("url") or ""
        if url in seen:
            continue
        seen.add(url)
        sources.append(
            {
                "n": index,
                "title": chunk.get("title") or "",
                "url": url,
                "date": chunk.get("published_at") or "",
                "score": chunk.get("score"),
            }
        )

    return {
        "query": query,
        "answer": answer,
        "sufficient": evidence.sufficient,
        "retrieval_mode": evidence.mode,
        "high_confidence_chunks": evidence.high_confidence,
        "required_high_confidence": evidence.required_confidence,
        "corpus_chunks": evidence.corpus_chunks,
        "sources": sources,
        "entities": evidence.entities,
        "related_cases": evidence.cases,
        "risk_flags": flags,
        "discover_recommended": not evidence.sufficient,
        "focus": evidence.focus,
        "on_topic": evidence.on_topic,
    }
