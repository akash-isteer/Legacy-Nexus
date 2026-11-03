"""Production configuration guard."""
import pytest
from pydantic import ValidationError

from app.config import Settings

STRONG = "x" * 48


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_development_allows_demo_values():
    s = _settings(environment="development", secret_key="dev-only-change-me-0123456789abcdefghijklmnopqrstuvwxyz",
                  bootstrap_admin_password="LegacyNexus@2026")
    assert s.environment == "development"


@pytest.mark.parametrize("override, message", [
    ({"secret_key": "dev-only-change-me-0123456789abcdefghijklmnopqrstuvwxyz"}, "SECRET_KEY"),
    ({"bootstrap_admin_password": "LegacyNexus@2026"}, "BOOTSTRAP_ADMIN_PASSWORD"),
])
def test_production_refuses_demo_values(override, message):
    with pytest.raises(ValidationError, match=message):
        _settings(**{"environment": "production", "secret_key": STRONG, **override})


def test_cloud_profile_requires_postgres():
    with pytest.raises(ValidationError, match="PostgreSQL"):
        _settings(deployment_profile="cloud", database_url="sqlite:///x.db")


def test_database_url_from_cloud_parts():
    s = _settings(deployment_profile="cloud", db_host="lnx-pg.postgres.database.azure.com", db_user="lnxadmin",
                  db_password="p@ss/word#1", db_name="legacynexus")
    assert s.database_url == ("postgresql+psycopg://lnxadmin:p%40ss%2Fword%231@lnx-pg.postgres.database.azure.com:5432/"
                              "legacynexus?sslmode=require")
    assert not s.is_sqlite


def test_unknown_profile_is_refused():
    with pytest.raises(ValidationError, match="DEPLOYMENT_PROFILE"):
        _settings(deployment_profile="hybrid")


def test_readiness_reports_every_backing_service(client):
    r = client.get("/api/health/ready")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["profile"] == "local"
    assert set(body["components"]) == {"database", "storage", "jobs", "vectors", "docqa_database", "docqa_vectors"}
    assert body["components"]["database"]["kind"] == "SQLite" and all(c["ok"] for c in body["components"].values())


def test_production_accepts_real_values():
    assert _settings(environment="production", secret_key=STRONG, bootstrap_admin_password="S0me-Real-Pass!").secret_key
