from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

import requests
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import Connection, Engine

from src.config import get_current_rms_config, get_database_url
from src.dashboard_data import ensure_schema

API_ENDPOINT = "https://api.current-rms.com/api/v1/opportunities"
STATES = ("orders", "drafts", "quotations")
PER_PAGE = 25
REQUEST_TIMEOUT_SECONDS = 30
MAX_PAGES_PER_STATE = 500
ADVISORY_LOCK_KEY = 820_241_117

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncResult:
    mode: str
    status: str
    rows_fetched: int
    rows_upserted: int
    rows_deleted: int
    started_at: datetime
    finished_at: datetime
    error: str | None = None


def parse_current_rms_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalised = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalised)
    return parsed.replace(tzinfo=None)


def prepare_opportunity_record(opportunity: dict[str, Any]) -> dict[str, Any]:
    member = opportunity.get("member") or {}
    starts_at = parse_current_rms_datetime(opportunity.get("starts_at"))
    updated_at = parse_current_rms_datetime(opportunity.get("updated_at"))
    return {
        "opportunity_id": str(opportunity.get("id", "")),
        "subject": opportunity.get("subject", ""),
        "starts_at": starts_at,
        "organisation": member.get("name", ""),
        "state": opportunity.get("state_name", ""),
        "status": opportunity.get("status_name", ""),
        "charge_total": opportunity.get("charge_total") or 0,
        "updated_at": updated_at,
    }


def _filter_mode(state: str) -> list[str]:
    if state == "orders":
        return ["orders", "not_cancelled"]
    return [state]


def _headers() -> dict[str, str]:
    config = get_current_rms_config()
    return {
        "X-AUTH-TOKEN": config["api_key"],
        "X-SUBDOMAIN": config["subdomain"],
        "X-TIME-ZONE": "Europe/London",
        "Content-Type": "application/json",
    }


def fetch_opportunities_for_state(
    session: requests.Session,
    state: str,
    *,
    updated_at_gt: datetime | None = None,
    endpoint: str = API_ENDPOINT,
) -> list[dict[str, Any]]:
    opportunities: list[dict[str, Any]] = []
    page = 1
    headers = _headers()

    while page <= MAX_PAGES_PER_STATE:
        params: dict[str, Any] = {
            "filtermode[]": _filter_mode(state),
            "page": page,
            "per_page": PER_PAGE,
        }
        if updated_at_gt is not None:
            params["q[updated_at_gt]"] = updated_at_gt.strftime(
                "%Y-%m-%dT%H:%M:%S.000Z"
            )

        response = session.get(
            endpoint,
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Current RMS returned {response.status_code}: {response.text}"
            )

        page_opportunities = response.json().get("opportunities", [])
        logger.info("Fetched %s %s opportunities on page %s", len(page_opportunities), state, page)
        if not page_opportunities:
            break
        opportunities.extend(page_opportunities)
        page += 1

    if page > MAX_PAGES_PER_STATE:
        raise RuntimeError(f"Exceeded {MAX_PAGES_PER_STATE} pages for {state}")
    return opportunities


def fetch_opportunities(
    mode: str,
    *,
    updated_at_gt: datetime | None,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    session = session or requests.Session()
    all_opportunities: list[dict[str, Any]] = []
    for state in STATES:
        all_opportunities.extend(
            fetch_opportunities_for_state(
                session,
                state,
                updated_at_gt=updated_at_gt if mode == "incremental" else None,
            )
        )
    return all_opportunities


def get_last_successful_read(conn: Connection, now: datetime) -> datetime:
    row = conn.execute(
        text(
            """
            SELECT time_of_read
            FROM api_reads
            ORDER BY time_of_read DESC
            LIMIT 1
            """
        )
    ).fetchone()
    if not row or not row[0]:
        return now - timedelta(hours=4)
    return row[0] - timedelta(hours=1)


def acquire_sync_lock(conn: Connection) -> bool:
    return bool(
        conn.execute(
            text("SELECT pg_try_advisory_lock(:lock_key)"),
            {"lock_key": ADVISORY_LOCK_KEY},
        ).scalar()
    )


def release_sync_lock(conn: Connection) -> None:
    conn.execute(
        text("SELECT pg_advisory_unlock(:lock_key)"),
        {"lock_key": ADVISORY_LOCK_KEY},
    )


def upsert_opportunities(conn: Connection, rows: Iterable[dict[str, Any]]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    statement = text(
        """
        INSERT INTO opportunities (
            opportunity_id, subject, starts_at, organisation, state, status,
            charge_total, updated_at
        ) VALUES (
            :opportunity_id, :subject, :starts_at, :organisation, :state, :status,
            :charge_total, :updated_at
        )
        ON CONFLICT (opportunity_id) DO UPDATE SET
            subject = EXCLUDED.subject,
            starts_at = EXCLUDED.starts_at,
            organisation = EXCLUDED.organisation,
            state = EXCLUDED.state,
            status = EXCLUDED.status,
            charge_total = EXCLUDED.charge_total,
            updated_at = EXCLUDED.updated_at
        """
    )
    conn.execute(statement, rows)
    return len(rows)


def ids_to_delete_for_full_sync(existing_ids: Iterable[str], api_ids: Iterable[str]) -> list[str]:
    return sorted(set(existing_ids) - set(api_ids))


def delete_stale_opportunities(conn: Connection, api_ids: list[str]) -> int:
    if not api_ids:
        raise RuntimeError("Full sync returned no opportunities; refusing to delete cache")
    existing_ids = [
        str(row[0])
        for row in conn.execute(text("SELECT opportunity_id FROM opportunities")).fetchall()
    ]
    stale_ids = ids_to_delete_for_full_sync(existing_ids, api_ids)
    if not stale_ids:
        return 0
    statement = text(
        "DELETE FROM opportunities WHERE opportunity_id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    result = conn.execute(statement, {"ids": stale_ids})
    return int(result.rowcount or 0)


def update_last_read(conn: Connection, read_time: datetime) -> None:
    conn.execute(text("DELETE FROM api_reads"))
    conn.execute(
        text("INSERT INTO api_reads (time_of_read) VALUES (:read_time)"),
        {"read_time": read_time},
    )


def record_sync_run(conn: Connection, result: SyncResult) -> None:
    conn.execute(
        text(
            """
            INSERT INTO sync_runs (
                mode, status, started_at, finished_at, duration_seconds,
                rows_fetched, rows_upserted, rows_deleted, error
            ) VALUES (
                :mode, :status, :started_at, :finished_at, :duration_seconds,
                :rows_fetched, :rows_upserted, :rows_deleted, :error
            )
            """
        ),
        {
            "mode": result.mode,
            "status": result.status,
            "started_at": result.started_at,
            "finished_at": result.finished_at,
            "duration_seconds": (
                result.finished_at - result.started_at
            ).total_seconds(),
            "rows_fetched": result.rows_fetched,
            "rows_upserted": result.rows_upserted,
            "rows_deleted": result.rows_deleted,
            "error": result.error,
        },
    )


def run_sync(
    mode: str,
    *,
    engine: Engine | None = None,
    session: requests.Session | None = None,
    now: datetime | None = None,
) -> SyncResult:
    if mode not in {"incremental", "full", "backfill"}:
        raise ValueError("mode must be incremental, full or backfill")

    started_at = now or datetime.now()
    engine = engine or create_engine(get_database_url(), pool_pre_ping=True)
    ensure_schema(engine)

    conn = engine.connect()
    locked = False
    try:
        locked = acquire_sync_lock(conn)
        conn.commit()
        if not locked:
            raise RuntimeError("Another sync is already running")

        updated_at_gt = None
        if mode == "incremental":
            updated_at_gt = get_last_successful_read(conn, started_at)
            conn.commit()
        raw_opportunities = fetch_opportunities(
            mode, updated_at_gt=updated_at_gt, session=session
        )
        rows = [prepare_opportunity_record(opp) for opp in raw_opportunities]

        with conn.begin():
            rows_upserted = upsert_opportunities(conn, rows)
            rows_deleted = 0
            if mode == "full":
                rows_deleted = delete_stale_opportunities(
                    conn, [row["opportunity_id"] for row in rows]
                )
                conn.execute(
                    text(
                        "INSERT INTO full_sync_logs (time_of_full_sync) VALUES (:sync_time)"
                    ),
                    {"sync_time": started_at},
                )
            update_last_read(conn, datetime.now())
            result = SyncResult(
                mode=mode,
                status="success",
                rows_fetched=len(raw_opportunities),
                rows_upserted=rows_upserted,
                rows_deleted=rows_deleted,
                started_at=started_at,
                finished_at=datetime.now(),
            )
            record_sync_run(conn, result)
        logger.info("Sync completed: %s", result)
        return result
    except Exception as exc:
        result = SyncResult(
            mode=mode,
            status="failed",
            rows_fetched=0,
            rows_upserted=0,
            rows_deleted=0,
            started_at=started_at,
            finished_at=datetime.now(),
            error=str(exc),
        )
        try:
            with conn.begin():
                record_sync_run(conn, result)
        except Exception:
            logger.exception("Could not record failed sync run")
        raise
    finally:
        if locked:
            try:
                release_sync_lock(conn)
            except Exception:
                logger.exception("Could not release sync advisory lock")
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync Current RMS opportunities")
    parser.add_argument(
        "--mode",
        choices=("incremental", "full", "backfill"),
        default="incremental",
        help=(
            "Sync mode to run. backfill fetches all pages and upserts rows without "
            "deleting cache entries."
        ),
    )
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args()
    try:
        run_sync(args.mode)
    except Exception:
        logger.exception("Current RMS sync failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
