# NYED sales dashboard

This repository contains the Streamlit refactor only.
The dashboard reads cached Postgres data only. Current RMS syncs run from a CLI
or GitHub Actions, not from the Streamlit request path.

## Run the Streamlit refactor locally

```bash
pip install -r requirements.txt
cp .env.example .env.local
docker compose up --build
```

Open http://localhost:8502.

Optional seed data for local development:

```bash
python -m src.seed_local
```

Run a manual Current RMS sync when `CURRENT_RMS_API_KEY` is configured:

```bash
python -m src.sync_current_rms --mode incremental
python -m src.sync_current_rms --mode full
python -m src.sync_current_rms --mode backfill
```

Run `backfill` after adding dashboard columns that depend on historical rows, such
as prior-year revenue. It fetches all Current RMS pages and upserts opportunities
without deleting local rows. Run `full` when you also want to remove stale cached
opportunities that are no longer returned by Current RMS.

## Streamlit Cloud secrets

Configure these as root-level Streamlit secrets:

```toml
DATABASE_URL = "postgresql://user:password@host/dbname?sslmode=require"
DASHBOARD_USERNAME = "..."
DASHBOARD_PASSWORD = "..."
```

`DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD` protect the Streamlit app with a
single shared login. `DASHBOARD_USERNAME` defaults to `admin`, but
`DASHBOARD_PASSWORD` must be configured before the dashboard will load.

The Streamlit app does not need `CURRENT_RMS_API_KEY` for normal operation.
Only the sync runner needs Current RMS credentials.

Streamlit Community Cloud may default to a newer Python runtime. If deployment
fails on package compatibility, set the app's Python version to `3.12` in the
Streamlit app settings.

## GitHub Actions sync

Required repository secrets:

- `CURRENT_RMS_API_KEY`
- `CURRENT_RMS_SUBDOMAIN`
- `NEON_DATABASE_URL_STAGING`
- `NEON_DATABASE_URL_PROD` later, after production cutover

The workflow runs incremental syncs every 30 minutes, a full sync daily, and
supports manual `incremental`, `full`, or `backfill` dispatches. Use a manual
`backfill` against staging and then production after deploying schema/query
changes that rely on historical Current RMS rows.
