import pytest
from pydantic import ValidationError

from app.config import Settings


def test_production_cannot_silently_use_memory_or_unencrypted_contacts():
    with pytest.raises(ValidationError, match="Production requires"):
        Settings(_env_file=None, environment="production", elasticsearch_url=None, elasticsearch_api_key=None)


@pytest.mark.parametrize("values", [{"elastic_inference_id": ""}, {"cors_origins": "*"},
    {"public_base_url": "https://secret@example.com"}, {"public_base_url": "https://example.com/path"}])
def test_unsafe_runtime_configuration_is_rejected(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)
