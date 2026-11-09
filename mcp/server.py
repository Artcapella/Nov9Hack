"""Read-only MCP tools over the live NETwork Elastic Cloud data.

Use this as a demo fallback or connect an Elastic Agent Builder agent through
its native MCP endpoint for the sponsor-facing version.
"""
import os

from elasticsearch import Elasticsearch
from fastmcp import FastMCP

app = FastMCP("NETwork Elastic tools")
client = Elasticsearch(os.environ["ELASTICSEARCH_URL"], api_key=os.environ["ELASTICSEARCH_API_KEY"])
PREFIX = os.getenv("ELASTICSEARCH_INDEX_PREFIX", "network")


@app.tool()
def find_conference_expert(question: str, limit: int = 5) -> list[dict]:
    """Hybrid-search opted-in attendee and sponsor expertise. Never returns contacts."""
    filters = [{"term": {"consent_to_discover": True}}, {"range": {"discoverable_until": {"gte": "now"}}}]
    response = client.search(
        index=f"{PREFIX}_user_vectors", size=limit,
        retriever={"rrf": {"retrievers": [
            {"standard": {"query": {"bool": {"must": [{"multi_match": {"query": question, "fields": ["profile_text^3", "headline", "can_help_with"]}}], "filter": filters}}}},
            {"standard": {"query": {"bool": {"must": [{"match": {"profile_semantic": question}}], "filter": filters}}}},
        ]}},
    )
    return [{"name": hit["_source"]["display_name"], "headline": hit["_source"]["headline"], "can_help_with": hit["_source"]["can_help_with"], "availability": hit["_source"]["availability"]} for hit in response["hits"]["hits"]]


@app.tool()
def conference_intelligence() -> list[dict]:
    """Use ES|QL to surface which opted-in skills are most available at the event."""
    query = f'''FROM {PREFIX}_user_vectors | WHERE consent_to_discover == true | STATS attendees = COUNT(*) BY availability | SORT attendees DESC'''
    response = client.esql.query(query=query)
    columns = [column["name"] for column in response["columns"]]
    return [dict(zip(columns, row, strict=True)) for row in response["values"]]


if __name__ == "__main__":
    app.run()
