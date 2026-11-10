import io

import pytest
from docx import Document
from pypdf import PdfWriter

from app.resume_parser import build_vector_text, extract_text, heuristic_parse


def test_parser_extracts_skills_and_excludes_contacts_from_structured_record():
    parsed = heuristic_parse("Jane Doe\njane@example.com\nhttps://linkedin.com/in/janedoe\nSkills: Python, Elasticsearch, Kubernetes\nProjects\nBuilt a RAG assistant")
    assert {"python", "elasticsearch", "kubernetes"}.issubset({skill.casefold() for skill in parsed.structured["skills"]})
    assert "jane@example.com" not in str(parsed.structured)
    assert parsed.contact_hints["email"] == "jane@example.com"


def test_vector_text_uses_only_reviewed_card_even_when_parser_suggested_more():
    profile = {"headline": "Engineer", "public_summary": "Builds search", "approved_experience": "Approved experience", "can_help_with": ["Elasticsearch"], "looking_for": ["Kubernetes"]}
    vector_text = build_vector_text(profile, {"skills": ["Python"], "project_highlights": ["Built a retrieval app"], "experience_highlights": []})
    assert "Elasticsearch" in vector_text
    assert "Built a retrieval app" not in vector_text
    assert "Approved experience" in vector_text


def test_multiline_sections_are_preserved_and_stop_at_the_next_heading():
    parsed = heuristic_parse("Demo Attendee\nExperience\nBuilt a Python service for many users\nProjects\nBuilt a retrieval assistant\nEducation\nStudied engineering")
    assert parsed.structured["experience_highlights"] == ["Built a Python service for many users"]
    assert parsed.structured["project_highlights"] == ["Built a retrieval assistant"]
    assert "Studied engineering" not in str(parsed.structured)


def test_docx_sections_and_unsupported_or_corrupt_files():
    doc = Document()
    for line in ["Demo Attendee", "Work Experience", "Delivered a reliable Python service"]:
        doc.add_paragraph(line)
    content = io.BytesIO(); doc.save(content)
    parsed = heuristic_parse(extract_text("resume.docx", content.getvalue()))
    assert parsed.structured["experience_highlights"] == ["Delivered a reliable Python service"]
    for filename in ("resume.exe", "resume.pdf", "resume.docx"):
        with pytest.raises(ValueError):
            extract_text(filename, b"invalid")


def test_empty_or_scanned_pdf_offers_manual_entry():
    writer = PdfWriter(); writer.add_blank_page(width=200, height=200)
    content = io.BytesIO(); writer.write(content)
    with pytest.raises(ValueError, match="manually"):
        extract_text("scan.pdf", content.getvalue())


def test_pdf_text_extraction_preserves_headings():
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer = PdfWriter(); page = writer.add_blank_page(width=400, height=400)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 360 Td (Demo Attendee) Tj 0 -20 Td (Experience) Tj 0 -20 Td (Built a Python service for users) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    content = io.BytesIO(); writer.write(content)
    assert heuristic_parse(extract_text("resume.pdf", content.getvalue())).structured["experience_highlights"] == ["Built a Python service for users"]


def test_glued_pdf_text_yields_headline_skills_and_linkedin_handle():
    text = ("Pat Example\nCity | pat@example.com | LinkedIn (pat-example)\nTECHNICAL SKILLS Languages: Python, SQL Tools: Git, Supabase\n"
            "EXPERIENCE Acme Labs — Research Assistant, City Apr 2025 – Present • Built an evaluation pipeline\nfor models\n"
            "PROJECTS NetVis | React, Vite • Built an interactive topology explorer")
    parsed = heuristic_parse(text)
    assert parsed.structured["headline"] == "Research Assistant · Acme Labs"
    assert {"Python", "SQL", "Git", "Supabase"}.issubset(parsed.structured["skills"])
    assert parsed.contact_hints["linkedin_url"] == "https://www.linkedin.com/in/pat-example"
    assert "Built an evaluation pipeline for models" in parsed.structured["experience_highlights"]
    assert any("topology explorer" in item for item in parsed.structured["project_highlights"])
