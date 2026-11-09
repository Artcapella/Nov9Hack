from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Keep every secret in .env, never in the client."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "NETwork Concierge"
    environment: str = "development"
    public_base_url: str = "http://localhost:8000"
    cors_origins: str = "http://localhost:8000"
    demo_event_id: str = "cnh-2026"
    profile_expiry_hours: int = 8

    elasticsearch_url: str | None = None
    elasticsearch_api_key: str | None = None
    elasticsearch_index_prefix: str = "network"
    elastic_inference_id: str | None = None
    elasticsearch_verify_certs: bool = True

    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"

    otel_exporter_otlp_endpoint: str | None = None
    otel_exporter_otlp_headers: str | None = None
    otel_service_name: str = "network-concierge"

    # Required in production to encrypt email / LinkedIn contact values at rest.
    contact_encryption_key: str | None = None

    @property
    def elastic_enabled(self) -> bool:
        return bool(self.elasticsearch_url and self.elasticsearch_api_key)

    def index(self, suffix: str) -> str:
        return f"{self.elasticsearch_index_prefix}_{suffix}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
