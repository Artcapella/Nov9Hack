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
    try:
        if name.endswith(".pdf"):
            text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)
        elif name.endswith(".docx"):
            # Bound expanded archives before python-docx opens them.
            from zipfile import ZipFile
            with ZipFile(io.BytesIO(content)) as archive:
                if sum(item.file_size for item in archive.infolist()) > 30_000_000:
                    raise ValueError("Expanded DOCX is too large. Use manual entry.")
            text = "\n".join(p.text for p in Document(io.BytesIO(content)).paragraphs)
        elif name.endswith((".txt", ".md")):
            text = content.decode("utf-8", errors="replace")
        else:
            raise ValueError("Use a PDF, DOCX, TXT, or Markdown resume.")
    except ValueError:
        raise
    except Exception:
        raise ValueError("Could not read this file. Try another format or enter your card manually.") from None
    if not text.strip():
        raise ValueError("No selectable text found. Enter your card manually; scanned PDFs need OCR.")
    return text


def heuristic_parse(text: str) -> ParsedResume:
    normalized = re.sub(r"\s+", " ", text).strip()
    lower = normalized.lower()
    skills = [term for term in SKILL_TERMS if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", lower)]
    # Preserve headings and boundaries; normalize inside the extracted fields.
    professional = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "", text)
    professional = re.sub(r"https?://(?:www\.)?linkedin\.com/[^\s|,]+", "", professional, flags=re.I)
    projects = _section_excerpt(professional, r"projects?|selected work", 700)
    experience = _section_excerpt(professional, r"(?:professional |work )?experience|employment|work history", 700)
    email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", normalized)
    linkedin = re.search(r"https?://(?:www\.)?linkedin\.com/in/[^\s|,]+", normalized, re.I)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return ParsedResume(
        text=normalized,
        structured={
            "skills": skills,
            "project_highlights": projects,
            "experience_highlights": experience,
            "parser": "local_heuristic_v2",
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
    excerpt = text[match.end():]
    excerpt = re.split(r"\n\s*(?:education|skills|certifications|awards|projects?|selected work|(?:professional |work )?experience|employment|work history)\s*(?::|\n|$)", excerpt, flags=re.I)[0][:limit]
    return [re.sub(r"\s+", " ", part.strip(" -•\t")) for part in re.split(r"\n|[•]", excerpt) if len(part.strip()) > 12][:6]


def build_vector_text(profile: dict, structured: dict | None = None) -> str:
    chunks = [
        profile.get("headline", ""), profile.get("public_summary", ""),
        profile.get("approved_experience", ""),
        "Can help with: " + ", ".join(profile.get("can_help_with", [])),
        "Looking for: " + ", ".join(profile.get("looking_for", [])),
    ]
    # Parser suggestions are restricted until explicitly copied into reviewed fields.
    return "\n".join(chunk for chunk in chunks if chunk.strip())[:6000]


def structured_json(parsed: ParsedResume) -> str:
    """Intentionally omits extracted contact hints from the structured resume record."""
    return json.dumps(parsed.structured)
