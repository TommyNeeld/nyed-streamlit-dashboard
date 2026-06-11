from src.config import get_database_url


def test_remote_database_url_uses_psycopg_ssl_and_connect_timeout(monkeypatch):
    get_database_url.cache_clear()
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:password@example.neon.tech/dbname?sslmode=require",
    )

    url = str(get_database_url())

    assert url.startswith("postgresql+psycopg://")
    assert "sslmode=require" in url
    assert "connect_timeout=15" in url


def test_remote_database_url_preserves_existing_connect_timeout(monkeypatch):
    get_database_url.cache_clear()
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:password@example.neon.tech/dbname?connect_timeout=30",
    )

    url = str(get_database_url())

    assert "connect_timeout=30" in url
    assert "connect_timeout=15" not in url
    assert "sslmode=require" in url


def test_local_database_url_does_not_add_remote_options(monkeypatch):
    get_database_url.cache_clear()
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:password@localhost/dbname",
    )

    url = str(get_database_url())

    assert url.startswith("postgresql+psycopg://")
    assert "sslmode=" not in url
    assert "connect_timeout=" not in url
