from app.resume_parser import build_vector_text, heuristic_parse


def test_parser_extracts_skills_and_excludes_contacts_from_structured_record():
    parsed = heuristic_parse("Jane Doe\njane@example.com\nhttps://linkedin.com/in/janedoe\nSkills: Python, Elasticsearch, Kubernetes\nProjects\nBuilt a RAG assistant")
    assert {"python", "elasticsearch", "kubernetes"}.issubset(parsed.structured["skills"])
    assert "jane@example.com" not in str(parsed.structured)
    assert parsed.contact_hints["email"] == "jane@example.com"


def test_vector_text_uses_only_approved_profile_fields_and_structured_highlights():
    profile = {"headline": "Engineer", "public_summary": "Builds search", "can_help_with": ["Elasticsearch"], "looking_for": ["Kubernetes"]}
    vector_text = build_vector_text(profile, {"skills": ["Python"], "project_highlights": ["Built a retrieval app"], "experience_highlights": []})
    assert "Elasticsearch" in vector_text
    assert "Built a retrieval app" in vector_text
