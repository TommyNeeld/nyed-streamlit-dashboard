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


def _needs_sslmode(url: str) -> bool:
    local_markers = ("localhost", "127.0.0.1", "@db:", "@db/")
    return url.startswith(("postgres://", "postgresql://")) and not any(
        marker in url for marker in local_markers
    )


@lru_cache(maxsize=1)
def get_database_url() -> str | URL:
    url = get_secret("DATABASE_URL")
    if url:
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        if _needs_sslmode(url) and "sslmode=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}sslmode=require"
        return url

    return URL.create(
        "postgresql",
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
