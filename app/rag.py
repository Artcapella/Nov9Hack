import json
import logging

from openai import AsyncOpenAI

from app.config import Settings
from app.store import NetworkStore

logger = logging.getLogger(__name__)


async def answer_with_evidence(question: str, store: NetworkStore, settings: Settings) -> dict:
    """Retrieval-first RAG. The fallback remains useful when no LLM key is present."""
    sources = await store.retrieve_knowledge(question)
    evidence = [
        {"title": item.get("title", "Conference profile"), "source_type": item.get("source_type", "knowledge"), "profile_id": item.get("profile_id"), "excerpt": item.get("content", "")[:420]}
        for item in sources
    ]
    if not settings.openai_api_key:
        return {
            "answer": "Retrieved the most relevant opt-in conference knowledge. Add OPENAI_API_KEY to enable a generated, cited concierge response.",
            "mode": "retrieval_only", "sources": evidence,
        }
    prompt = (
        "You are NETwork Concierge for an in-person conference. Answer only from the supplied opt-in knowledge. "
        "Never invent people, claims, or contact details. Recommend introductions only when source evidence supports it. "
        "Use short source labels like [1], [2].\n\n"
        f"Question: {question}\n\nSources:\n{json.dumps(evidence)}"
    )
    try:
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        response = await client.chat.completions.create(
            model=settings.openai_model,
            temperature=0.2,
            messages=[{"role": "system", "content": "Grounded conference concierge. Respect consent and privacy."}, {"role": "user", "content": prompt}],
        )
        return {"answer": response.choices[0].message.content, "mode": "retrieval_augmented_generation", "sources": evidence}
    except Exception as exc:
        logger.warning("LLM generation unavailable; returning retrieved evidence: %s", exc)
        return {"answer": "Retrieved evidence only; generation was unavailable.", "mode": "retrieval_only", "sources": evidence}
