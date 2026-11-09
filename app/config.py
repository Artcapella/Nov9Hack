from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Keep every secret in .env, never in the client."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "NETwork Concierge"
    environment: str = "development"
    public_base_url: str = "http://localhost:8000"
    cors_origins: str = "http://localhost:8000"
    demo_event_id: str = "cnh-2026"
    profile_expiry_hours: int = Field(default=8, ge=1, le=72)
    session_hours: int = Field(default=24, ge=1, le=168)
    draft_minutes: int = Field(default=20, ge=1, le=60)

    elasticsearch_url: str | None = None
    elasticsearch_api_key: str | None = None
    elasticsearch_index_prefix: str = "network"
    elastic_inference_id: str = ".jina-embeddings-v5-text-small"
    elastic_schema_version: str = "v3"
    elastic_bootstrap_on_start: bool = False
    elasticsearch_verify_certs: bool = True

    kibana_url: str | None = None
    agent_builder_api_key: str | None = None
    agent_builder_agent_id: str = "network-concierge"
    agent_builder_enabled: bool = False
    # Administrative imports only. Exact hostnames, never wildcards.
    resume_source_hosts: list[str] = Field(default_factory=list)
    import_max_bytes: int = Field(default=6_000_000, ge=1, le=6_000_000)
    import_min_interval_seconds: float = Field(default=1.0, ge=0, le=60)
    worker_poll_seconds: float = Field(default=3.0, ge=0.1, le=60)
    worker_lease_seconds: int = Field(default=180, ge=30, le=600)
    worker_max_attempts: int = Field(default=3, ge=1, le=10)

    otel_exporter_otlp_endpoint: str | None = None
    otel_exporter_otlp_headers: str | None = None
    otel_service_name: str = "network-api"
    dash0_otlp_http_base: str | None = None
    dash0_auth_token: str | None = None
    dash0_dataset: str = "default"
    app_version: str = "dev"

    # Required in production to encrypt email / LinkedIn contact values at rest.
    contact_encryption_key: str | None = None
    # Organizer-only operations view. Required whenever Elasticsearch or production is enabled.
    ops_token: str | None = Field(default=None, min_length=16)

    @model_validator(mode="after")
    def validate_runtime(self):
        if not self.elastic_inference_id.strip():
            raise ValueError("Pin ELASTIC_INFERENCE_ID to an inspected inference endpoint")
        public = urlsplit(self.public_base_url)
        if public.scheme not in {"http", "https"} or not public.hostname or public.username or public.password or public.path not in {"", "/"} or public.query or public.fragment:
            raise ValueError("PUBLIC_BASE_URL must be an HTTP(S) origin without credentials or a path")
        if "*" in self.cors_origins.split(","):
            raise ValueError("Authenticated sessions require explicit CORS origins")
        if self.environment == "production":
            if not self.elastic_enabled or not self.contact_encryption_key:
                raise ValueError("Production requires Elasticsearch and CONTACT_ENCRYPTION_KEY")
            if not self.public_base_url.startswith("https://"):
                raise ValueError("Production requires an HTTPS PUBLIC_BASE_URL")
        return self

    @property
    def elastic_enabled(self) -> bool:
        return bool(self.elasticsearch_url and self.elasticsearch_api_key)

    def index(self, suffix: str) -> str:
        # New aliases avoid silently mixing the prototype's default model/schema.
        return f"{self.elasticsearch_index_prefix}_{suffix}_{self.elastic_schema_version}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
