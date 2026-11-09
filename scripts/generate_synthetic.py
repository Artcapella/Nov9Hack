"""Generate the deterministic SynthDemo cohort: python -m scripts.generate_synthetic [--count 160]

Every card is fictional. Bootstrap tags them dataset=SynthDemo so the UI can hide them
and introductions to them are disabled. Names are combinations of common given and family names.
"""
import argparse
import json
import random
from pathlib import Path

FIRST = ["Ada", "Bea", "Cal", "Dara", "Eli", "Fen", "Gus", "Hana", "Ivo", "Jae", "Kai", "Lin", "Mo", "Nell", "Oz", "Pia", "Quin",
         "Rafa", "Sol", "Tam", "Uma", "Vik", "Wren", "Xia", "Yara", "Zev", "Amara", "Bodhi", "Chidi", "Dev", "Esme", "Femi",
         "Gia", "Haru", "Ines", "Jonah", "Kemi", "Luca", "Mei", "Nico", "Oona", "Priya", "Rory", "Sana", "Teo", "Vera"]
LAST = ["Abara", "Berg", "Castro", "Dube", "Eriksen", "Farah", "Gallo", "Hoang", "Iyer", "Jensen", "Kowal", "Lopez", "Mbeki",
        "Novak", "Okoye", "Petrov", "Quispe", "Rossi", "Sato", "Tran", "Ueda", "Varga", "Wiles", "Yilmaz", "Zhou", "Adeyemi",
        "Brennan", "Cho", "Dimitrov", "Esposito", "Fischer", "Gupta", "Haddad", "Ito", "Kaur", "Lindgren", "Moreno", "Nakamura"]

ARCHETYPES = {
    "web": {
        "titles": ["Frontend engineer", "Full-stack developer", "Web performance engineer", "Design-systems engineer", "Accessibility specialist"],
        "skills": ["React", "TypeScript", "Next.js", "CSS", "accessibility", "Node", "web performance", "GraphQL", "Svelte", "HTML"],
        "projects": ["a component library used by six product teams", "an offline-first event schedule PWA", "a real-time collaborative whiteboard",
                     "a WCAG audit tool for design tokens", "server-side rendering for a large marketplace"],
        "seeking": ["backend mentorship", "design feedback", "observability for frontends", "a co-founder", "open-source contributors"],
    },
    "design": {
        "titles": ["Product designer", "UX researcher", "Product manager", "Design lead", "Growth PM"],
        "skills": ["UX research", "Figma", "prototyping", "product strategy", "usability testing", "growth", "service design", "roadmapping"],
        "projects": ["onboarding research for a fintech app", "a prototype for accessible voting kiosks", "a pricing experiment framework",
                     "journey maps for hospital discharge", "a design critique ritual for remote teams"],
        "seeking": ["engineers to prototype with", "data for research", "AI product ideas", "user interview partners", "frontend collaborators"],
    },
    "systems": {
        "titles": ["Platform engineer", "Site reliability engineer", "DevOps engineer", "Cloud architect", "Infrastructure lead"],
        "skills": ["Kubernetes", "Terraform", "OpenTelemetry", "observability", "Docker", "Helm", "incident response", "AWS", "networking", "Go"],
        "projects": ["a multi-cluster Kubernetes migration", "SLO dashboards with OpenTelemetry", "a self-service platform with Helm charts",
                     "cutting cloud spend by right-sizing", "chaos testing for a payments API"],
        "seeking": ["ML deployment problems", "frontend observability", "security reviews", "SRE mentees", "product feedback"],
    },
    "data": {
        "titles": ["Data engineer", "Analytics engineer", "Data scientist", "Search engineer", "BI developer"],
        "skills": ["SQL", "Spark", "dbt", "Elasticsearch", "data pipelines", "analytics", "ETL", "Airflow", "Python", "data warehouse"],
        "projects": ["a streaming ETL pipeline for IoT sensors", "hybrid search for a legal archive", "a dbt project with data contracts",
                     "churn analytics for a SaaS product", "a lakehouse migration"],
        "seeking": ["ML collaborators", "data visualisation help", "Kubernetes deployment advice", "real-time search ideas", "mentorship"],
    },
    "ai": {
        "titles": ["ML engineer", "AI researcher", "Applied scientist", "LLM engineer", "MLOps engineer"],
        "skills": ["PyTorch", "LLMs", "RAG", "embeddings", "NLP", "MLOps", "vector search", "Python", "fine-tuning", "evaluation"],
        "projects": ["a RAG assistant over internal docs", "evaluation harness for LLM agents", "fine-tuning a small model for triage",
                     "embedding-based recommendations", "a multimodal search prototype"],
        "seeking": ["GPU infrastructure help", "product designers", "labelled datasets", "MLOps feedback", "privacy reviews"],
    },
    "security": {
        "titles": ["Security engineer", "Privacy engineer", "AppSec lead", "Identity engineer", "Compliance engineer"],
        "skills": ["threat modeling", "IAM", "privacy", "authentication", "cryptography", "compliance", "pen testing", "zero trust"],
        "projects": ["passkey rollout for a consumer app", "privacy reviews for an AI feature", "threat models for a Kubernetes platform",
                     "SOC 2 automation", "a secrets-scanning pipeline"],
        "seeking": ["engineers who want security reviews", "AI safety collaborators", "platform partners", "mentees", "policy feedback"],
    },
}
INTERESTS = ["bouldering", "jazz", "chess", "trail running", "sourdough", "film photography", "board games", "cycling", "salsa dancing",
             "birdwatching", "pottery", "sci-fi novels", "volunteering", "gardening", "surfing", "karaoke", "speedcubing", "hiking", "tea", "drawing"]
AVAILABILITY = ["now", "after-lunch", "later"]


def card(rng: random.Random) -> dict:
    primary = rng.choice(list(ARCHETYPES))
    blend = rng.choice([a for a in ARCHETYPES if a != primary]) if rng.random() < 0.35 else None
    a, b = ARCHETYPES[primary], ARCHETYPES[blend] if blend else None
    skills = rng.sample(a["skills"], 3) + (rng.sample(b["skills"], 2) if b else rng.sample(a["skills"], 1))
    skills = list(dict.fromkeys(skills))
    title = rng.choice(a["titles"])
    project = rng.choice(a["projects"]) if not b or rng.random() < 0.6 else rng.choice(b["projects"])
    years = rng.randint(1, 15)
    roles = ["peer"]
    if years >= 8 and rng.random() < 0.5:
        roles.append("mentor")
    elif years <= 2 and rng.random() < 0.6:
        roles.append("mentee")
    if rng.random() < 0.05:
        roles = ["recruiter"]
        title = f"Technical recruiter · {primary} teams"
    name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
    headline = f"{title} · {skills[0]}" + (f" + {skills[-1]}" if blend else "")
    summary = (f"I work on {project}. {years} years in {primary if primary != 'ai' else 'AI / ML'}"
               + (f", lately crossing into {blend if blend != 'ai' else 'AI'}." if blend else ".")
               + f" Happy to talk about {skills[1]} and {skills[2] if len(skills) > 2 else skills[0]}.")
    return {
        "display_name": name,
        "headline": headline[:160],
        "public_summary": summary,
        "approved_experience": f"{title}, {years} years. Built {project}.",
        "can_help_with": skills,
        "looking_for": rng.sample(a["seeking"], 2) if roles != ["recruiter"] else [f"{primary} engineers", "candidates open to new roles"],
        "interests": rng.sample(INTERESTS, rng.randint(1, 3)),
        "roles": roles,
        "formats": sorted(set(rng.sample(["async", "one_to_one", "small_group"], rng.randint(1, 3)))),
        "availability": rng.choice(AVAILABILITY),
    }


def generate(count: int, seed: int = 2026) -> list[dict]:
    rng = random.Random(seed)
    return [card(rng) for _ in range(count)]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=160)
    parser.add_argument("--out", default="data/synthetic_profiles.json")
    args = parser.parse_args()
    cards = generate(args.count)
    Path(args.out).write_text(json.dumps(cards, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(cards)} synthetic cards to {args.out}")
