"""Administrative, allowlisted corpus acquisition. Never creates attendees."""
import asyncio
import hashlib
import http.client
import ipaddress
import json
import socket
import ssl
import threading
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from app.config import Settings
from app.resume_parser import ParsedResume, extract_text, heuristic_parse

_locks: dict[str, threading.Lock] = {}
_last_fetch: dict[str, float] = {}
_guard = threading.Lock()


def source_record(content: bytes, source_type: str, basis: str, url: str | None = None,
                  license: str | None = None, owner_id: str = "corpus") -> dict:
    digest = hashlib.sha256(content).hexdigest()
    source_id = hashlib.sha256(f"{owner_id}\0{source_type}\0{url or ''}\0{digest}".encode()).hexdigest()
    return {"source_id": source_id, "source_type": source_type, "source_url": url,
            "acquisition_basis": basis, "license": license, "content_hash": digest,
            "parser_version": "local_heuristic_v2", "acquired_at": datetime.now(UTC).isoformat()}


def resolve_public(url: str, allowed_hosts: list[str]) -> tuple[object, str]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise ValueError("Use a public HTTP(S) URL without credentials, query, or fragment")
    host = parsed.hostname.lower()
    if host not in {item.lower() for item in allowed_hosts} or parsed.port not in {None, 80 if parsed.scheme == "http" else 443}:
        raise ValueError("Source hostname or port is not allowlisted")
    records = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    addresses = [record[4][0] for record in records]
    if not addresses or any(not ipaddress.ip_address(address).is_global or ipaddress.ip_address(address).is_multicast or ipaddress.ip_address(address).is_reserved for address in addresses):
        raise ValueError("Source must resolve exclusively to public addresses")
    return parsed, addresses[0]


class PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, address, tls, timeout):
        super().__init__(host, port=port, timeout=timeout)
        self.address, self.tls = address, tls

    def connect(self):
        # Connect to the validated IP; never resolve again between check and use.
        sock = socket.create_connection((self.address, self.port), self.timeout)
        self.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host) if self.tls else sock


def download(url: str, settings: Settings) -> tuple[bytes, str, str]:
    started = time.monotonic()
    for _ in range(4):
        parsed, address = resolve_public(url, settings.resume_source_hosts)
        host = parsed.hostname.lower()
        with _guard:
            lock = _locks.setdefault(host, threading.Lock())
        with lock:
            wait = settings.import_min_interval_seconds - (time.monotonic() - _last_fetch.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            _last_fetch[host] = time.monotonic()
            connection = PinnedHTTP(host, parsed.port or (443 if parsed.scheme == "https" else 80), address, parsed.scheme == "https", 8)
            try:
                connection.request("GET", parsed.path or "/", headers={"User-Agent": "NETwork-Research-Importer/2.0", "Accept-Encoding": "identity"})
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location:
                        raise ValueError("Redirect lacks a destination")
                    url = urljoin(url, location)
                    if parsed.scheme == "https" and urlsplit(url).scheme != "https":
                        raise ValueError("HTTPS redirects must remain HTTPS")
                    continue  # Validate host, DNS, port and protocol at every hop.
                if response.status != 200:
                    raise ValueError("Source did not return a successful response")
                if response.getheader("Content-Encoding") not in {None, "identity"}:
                    raise ValueError("Compressed source responses are not supported")
                media = (response.getheader("Content-Type") or "").split(";", 1)[0].lower()
                if media not in {"text/plain", "text/markdown", "text/html", "application/json", "application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}:
                    raise ValueError("Unsupported source content type")
                length = response.getheader("Content-Length")
                if length and int(length) > settings.import_max_bytes:
                    raise ValueError("Source exceeds the maximum size")
                content = bytearray()
                while True:
                    if time.monotonic() - started > 30:
                        raise ValueError("Source exceeded the download deadline")
                    chunk = response.read(min(65536, settings.import_max_bytes + 1 - len(content)))
                    content.extend(chunk)
                    if len(content) > settings.import_max_bytes:
                        raise ValueError("Source exceeds the maximum size")
                    if not chunk:
                        break
                return bytes(content), media, url
            finally:
                connection.close()
    raise ValueError("Too many source redirects")


class ProfessionalHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1
        if tag in {"p", "div", "h1", "h2", "h3", "li", "br", "section"} and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self.hidden:
            self.hidden -= 1
        if tag in {"p", "div", "h1", "h2", "h3", "li", "section"} and not self.hidden:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


async def import_source(store, entry: dict, fetcher=download) -> dict:
    if not entry.get("acquisition_basis") or not entry.get("license") or entry.get("source_type") not in {"web", "download"}:
        raise ValueError("Each manifest entry needs source_type, acquisition_basis and license")
    content, media, final_url = await asyncio.to_thread(fetcher, entry["url"], store.settings)
    if entry.get("format") == "jsonresume":
        # Some upstream sample resumes contain literal newlines in JSON strings.
        data = json.loads(content.decode("utf-8"), strict=False)
        def clean(value):
            return " ".join(str(value).split())[:700]
        skills = [clean(keyword) for skill in data.get("skills", [])[:30]
                  for keyword in [skill.get("name", "")] + skill.get("keywords", [])[:10] if keyword]
        def highlights(field):
            return [clean(value) for item in data.get(field, [])[:10]
                    for value in [item.get("position", ""), item.get("description", ""), item.get("summary", "")] + item.get("highlights", [])[:6] if value][:20]
        # Exclude basics, addresses, references, and every contact field entirely.
        parsed = ParsedResume(text="", structured={"skills": skills, "experience_highlights": highlights("work"),
                               "project_highlights": highlights("projects"), "parser": "jsonresume_v1"}, contact_hints={})
    elif media == "text/html":
        parser = ProfessionalHTML()
        parser.feed(content.decode("utf-8", errors="replace"))
        text = "".join(parser.parts)
        parsed = heuristic_parse(text)
    else:
        suffix = {"application/pdf": ".pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx"}.get(media, ".txt")
        text = extract_text("resume" + suffix, content)
        parsed = heuristic_parse(text)
    source = source_record(content, entry["source_type"], entry["acquisition_basis"], final_url, entry["license"])
    source["parser_version"] = parsed.structured["parser"]
    resume_id = source["source_id"]
    await store.put("sources", resume_id, source)
    await store.put("resume_structured", resume_id, {"resume_id": resume_id, "source_id": resume_id,
                    "structured": parsed.structured, "approval_version": 0, "created_at": source["acquired_at"]})
    job_id = f"corpus-{resume_id}"
    await store.put("ingestion_jobs", job_id, {"job_id": job_id, "resume_id": resume_id, "source_id": resume_id,
                    "event_id": store.settings.demo_event_id, "kind": "corpus", "state": "queued", "attempts": 0,
                    "approval_version": 0, "created_at": source["acquired_at"], "trace_context": {}}, create=True)
    return {"source_id": resume_id, "job_id": job_id}
