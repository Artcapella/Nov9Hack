"""Opt-in resume parsing: source files are processed in memory and never retained."""
import io
import json
import re
from dataclasses import dataclass

from docx import Document
from pypdf import PdfReader

SKILL_TERMS = [
    "python", "fastapi", "javascript", "typescript", "react", "next.js", "sql",
    "elasticsearch", "kubernetes", "docker", "helm", "opentelemetry", "dash0",
    "aws", "gcp", "azure", "machine learning", "data science", "postgresql",
    "terraform", "linux", "go", "rust", "java", "security", "devops", "mcp",
]


@dataclass
class ParsedResume:
    text: str
    structured: dict
    contact_hints: dict


def extract_text(filename: str, content: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)
    if name.endswith(".docx"):
        return "\n".join(p.text for p in Document(io.BytesIO(content)).paragraphs)
    if name.endswith((".txt", ".md")):
        return content.decode("utf-8", errors="replace")
    raise ValueError("Use a PDF, DOCX, TXT, or Markdown resume.")


def heuristic_parse(text: str) -> ParsedResume:
    normalized = re.sub(r"\s+", " ", text).strip()
    lower = normalized.lower()
    skills = [term for term in SKILL_TERMS if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", lower)]
    projects = _section_excerpt(normalized, r"projects?|selected work", 700)
    experience = _section_excerpt(normalized, r"experience|employment|work history", 700)
    email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", normalized)
    linkedin = re.search(r"https?://(?:www\.)?linkedin\.com/in/[^\s|,]+", normalized, re.I)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return ParsedResume(
        text=normalized,
        structured={
            "skills": skills,
            "project_highlights": projects,
            "experience_highlights": experience,
            "parser": "local_heuristic_v1",
        },
        contact_hints={
            "email": email.group(0) if email else None,
            "linkedin_url": linkedin.group(0) if linkedin else None,
            "suggested_name": lines[0][:80] if lines else None,
        },
    )


def _section_excerpt(text: str, heading: str, limit: int) -> list[str]:
    match = re.search(rf"(?:^|\n)\s*(?:{heading})\s*[:\n]", text, re.I)
    if not match:
        return []
    excerpt = text[match.end(): match.end() + limit]
    excerpt = re.split(r"\n\s*(?:education|skills|certifications|awards)\b", excerpt, flags=re.I)[0]
    return [part.strip(" -•\t") for part in re.split(r"\n|[•]", excerpt) if len(part.strip()) > 12][:6]


def build_vector_text(profile: dict, structured: dict | None = None) -> str:
    chunks = [
        profile.get("headline", ""), profile.get("public_summary", ""),
        "Can help with: " + ", ".join(profile.get("can_help_with", [])),
        "Looking for: " + ", ".join(profile.get("looking_for", [])),
    ]
    if structured:
        chunks.append("Resume skills: " + ", ".join(structured.get("skills", [])))
        chunks.extend(structured.get("project_highlights", []))
        chunks.extend(structured.get("experience_highlights", []))
    return "\n".join(chunk for chunk in chunks if chunk.strip())[:6000]


def structured_json(parsed: ParsedResume) -> str:
    """Intentionally omits extracted contact hints from the structured resume record."""
    return json.dumps(parsed.structured)
