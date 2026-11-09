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
    "pytorch", "pandas", "hugging face", "node.js", "flask", "html", "css", "tableau", "nlp", "llm", "rag",
    "computer vision", "spark", "dbt", "figma", "tensorflow", "scikit-learn", "supabase", "mysql", "git",
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


HEADINGS = r"education|(?:technical )?skills|certifications|awards|publications|leadership|projects?|selected work|(?:professional |work |research )?experience|employment|work history"


def normalize(text: str) -> str:
    """Repair common PDF extraction artifacts: one word per line, doubled spaces, headings glued to content."""
    text = text.replace("\r", "")
    text = re.sub(r"[ \t]*\n[ \t]*\n[ \t]*(?=[^\s\n])", " ", text) if text.count("\n \n") > 20 else text
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +([,.;:)])", r"\1", text)
    # All-caps headings (EXPERIENCE, TECHNICAL SKILLS) start their own line even when the PDF glued them to content.
    text = re.sub(rf"[ \t]*\b({HEADINGS.upper().replace('(?:', '(?:')})\b[ \t]*(?=\S)", lambda m: f"\n{m.group(1)}\n", text)
    return text


def _skills_section(text: str) -> list[str]:
    section = re.search(rf"(?:^|\n)\s*(?:technical skills|skills)\s*[:\n]?(.*?)(?=\n\s*(?:{HEADINGS})\s*(?::|\n|$)|\Z)", text, re.I | re.S)
    if not section:
        return []
    body = re.sub(r"\b[A-Z][a-z]+(?: [A-Za-z&/]+)?:\s*", ",", section.group(1))  # "Languages:", "Developer Tools:"
    items = [re.sub(r"\s+", " ", item).strip(" .;•-\t") for item in re.split(r"[,;\n•|]", body)]
    return [item for item in items if 1 <= len(item) <= 30][:30]


def _headline(experience: list[str]) -> str | None:
    if not experience:
        return None
    first = experience[0]
    parts = re.split(r"\s+[—–-]\s+", first, maxsplit=1)
    if len(parts) == 2:
        org, rest = parts
        title = rest.split(",")[0].strip()
        if 2 < len(title) < 70 and 1 < len(org) < 70:
            return f"{title} · {org.strip()}"
    return None


def heuristic_parse(text: str) -> ParsedResume:
    text = normalize(text)
    normalized = re.sub(r"\s+", " ", text).strip()
    lower = normalized.lower()
    matched = [term for term in SKILL_TERMS if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", lower)]
    listed = _skills_section(text)
    seen, skills = set(), []
    for skill in listed + matched:
        if skill.casefold() not in seen:
            seen.add(skill.casefold())
            skills.append(skill)
    # Preserve headings and boundaries; normalize inside the extracted fields.
    professional = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "", text)
    professional = re.sub(r"https?://(?:www\.)?linkedin\.com/[^\s|,]+", "", professional, flags=re.I)
    projects = _section_excerpt(professional, r"projects?|selected work", 1500)
    experience = _section_excerpt(professional, r"(?:professional |work |research )?experience|employment|work history", 1500)
    email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", normalized)
    linkedin = re.search(r"https?://(?:www\.)?linkedin\.com/in/[^\s|,)]+", normalized, re.I)
    handle = None if linkedin else re.search(r"(?:linkedin\.com/in/|linkedin\s*[:(]\s*)([A-Za-z0-9-]{3,100})", normalized, re.I)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    return ParsedResume(
        text=normalized,
        structured={
            "skills": skills[:25],
            "project_highlights": projects,
            "experience_highlights": experience,
            "headline": _headline(experience),
            "parser": "local_heuristic_v3",
        },
        contact_hints={
            "email": email.group(0) if email else None,
            "linkedin_url": linkedin.group(0) if linkedin else f"https://www.linkedin.com/in/{handle.group(1)}" if handle else None,
            "suggested_name": lines[0][:80] if lines else None,
        },
    )


def _section_excerpt(text: str, heading: str, limit: int) -> list[str]:
    match = re.search(rf"(?:^|\n)\s*(?:{heading})\s*[:\n]", text, re.I)
    if not match:
        return []
    excerpt = text[match.end():]
    excerpt = re.split(rf"\n\s*(?:{HEADINGS})\s*(?::|\n|$)", excerpt, flags=re.I)[0][:limit]
    # Bulleted resumes: one item per bullet, wrapped lines rejoined. Otherwise one item per line.
    pieces = re.split(r"[•●▪]", excerpt) if re.search(r"[•●▪]", excerpt) else excerpt.split("\n")
    parts = [re.sub(r"\s+", " ", part).strip(" -.,;\t\n") for part in pieces]
    return [part[:240] for part in parts if len(part) > 12][:8]


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
