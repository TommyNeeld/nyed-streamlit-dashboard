import os
from functools import lru_cache

from dotenv import load_dotenv
from sqlalchemy import URL


def load_runtime_env() -> None:
    """Load local env files after process env so deployed secrets still win."""
    load_dotenv(".env.local")
    load_dotenv(".env")


def get_secret(name: str, default: str | None = None) -> str | None:
    load_runtime_env()
    value = os.getenv(name)
    if value:
        return value

    try:
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass

    return default


def _is_remote_postgres_url(url: str) -> bool:
    local_markers = ("localhost", "127.0.0.1", "@db:", "@db/")
    return url.startswith(
        ("postgres://", "postgresql://", "postgresql+psycopg://")
    ) and not any(marker in url for marker in local_markers)


def _append_url_param(url: str, name: str, value: str) -> str:
    if f"{name}=" in url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{name}={value}"


@lru_cache(maxsize=1)
def get_database_url() -> str | URL:
    url = get_secret("DATABASE_URL")
    if url:
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        if _is_remote_postgres_url(url):
            url = _append_url_param(url, "sslmode", "require")
            url = _append_url_param(url, "connect_timeout", "15")
        return url

    return URL.create(
        "postgresql+psycopg",
        username=get_secret("PGUSER"),
        password=get_secret("PGPASSWORD"),
        host=get_secret("PGHOST", "localhost"),
        port=int(get_secret("PGPORT", "5432")),
        database=get_secret("PGDATABASE"),
    )


def get_current_rms_config() -> dict[str, str]:
    api_key = get_secret("CURRENT_RMS_API_KEY") or get_secret("API_KEY")
    subdomain = get_secret("CURRENT_RMS_SUBDOMAIN", "nyed")
    if not api_key:
        raise RuntimeError("Missing CURRENT_RMS_API_KEY or API_KEY")
    return {"api_key": api_key, "subdomain": subdomain}
