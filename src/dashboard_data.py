from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Iterable

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from src.config import get_database_url

CONFIRMED_STATUSES = ["Completed", "Active", "Provisional", "Open", "Postponed"]


@dataclass(frozen=True)
class DashboardSnapshot:
    confirmed: pd.DataFrame
    jobs: pd.DataFrame
    reserved: pd.DataFrame
    quote: pd.DataFrame
    callouts: dict[str, float | None]
    sync_status: dict[str, object | None]
    generated_at: datetime


def get_engine() -> Engine:
    return create_engine(get_database_url(), pool_pre_ping=True)


def ensure_schema(engine_or_conn: Engine | Connection | None = None) -> None:
    engine_or_conn = engine_or_conn or get_engine()
    statements = [
        """
        CREATE TABLE IF NOT EXISTS opportunities (
            id SERIAL PRIMARY KEY,
            opportunity_id VARCHAR UNIQUE NOT NULL,
            subject VARCHAR,
            starts_at TIMESTAMP,
            organisation VARCHAR,
            state VARCHAR,
            status VARCHAR,
            charge_total DECIMAL,
            updated_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS targets (
            id SERIAL PRIMARY KEY,
            target_date DATE NOT NULL,
            amount INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS api_reads (
            id SERIAL PRIMARY KEY,
            time_of_read TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sync_runs (
            id SERIAL PRIMARY KEY,
            mode VARCHAR NOT NULL,
            status VARCHAR NOT NULL,
            started_at TIMESTAMP NOT NULL,
            finished_at TIMESTAMP NOT NULL,
            duration_seconds DECIMAL,
            rows_fetched INTEGER DEFAULT 0,
            rows_upserted INTEGER DEFAULT 0,
            rows_deleted INTEGER DEFAULT 0,
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS full_sync_logs (
            id SERIAL PRIMARY KEY,
            time_of_full_sync TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_opportunities_starts_at ON opportunities (starts_at)",
        "CREATE INDEX IF NOT EXISTS idx_opportunities_updated_at ON opportunities (updated_at)",
        "CREATE INDEX IF NOT EXISTS idx_targets_target_date ON targets (target_date)",
        "CREATE INDEX IF NOT EXISTS idx_sync_runs_finished_at ON sync_runs (finished_at)",
    ]

    if isinstance(engine_or_conn, Engine):
        with engine_or_conn.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))
    else:
        for statement in statements:
            engine_or_conn.execute(text(statement))


def _month_start(value: date | datetime) -> date:
    return date(value.year, value.month, 1)


def add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def month_window(today: date, months_back: int) -> list[date]:
    start = add_months(_month_start(today), -months_back)
    end = add_months(_month_start(today), 2)
    months = []
    current = start
    while current <= end:
        months.append(current)
        current = add_months(current, 1)
    return months


def select_latest_targets(targets: pd.DataFrame) -> dict[date, int]:
    if targets.empty:
        return {}

    working = targets.copy()
    working["target_date"] = pd.to_datetime(working["target_date"]).dt.date
    working["created_at"] = pd.to_datetime(working["created_at"], errors="coerce")
    working = working.sort_values(["target_date", "created_at"])
    latest = working.groupby("target_date", as_index=False).tail(1)
    return {row.target_date: int(row.amount) for row in latest.itertuples()}


def _normalise_opportunities(opportunities: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "opportunity_id",
        "subject",
        "starts_at",
        "organisation",
        "state",
        "status",
        "charge_total",
        "updated_at",
    ]
    if opportunities.empty:
        df = pd.DataFrame(columns=columns + ["month"])
        df["starts_at"] = pd.to_datetime(df["starts_at"])
        df["updated_at"] = pd.to_datetime(df["updated_at"])
        df["charge_total"] = pd.to_numeric(df["charge_total"])
        df["month"] = pd.to_datetime(df["month"])
        return df

    df = opportunities.copy()
    for column in columns:
        if column not in df.columns:
            df[column] = None
    df["starts_at"] = pd.to_datetime(df["starts_at"], errors="coerce")
    df["updated_at"] = pd.to_datetime(df["updated_at"], errors="coerce")
    df["charge_total"] = pd.to_numeric(df["charge_total"], errors="coerce").fillna(0)
    df["month"] = df["starts_at"].dt.to_period("M").dt.to_timestamp()
    return df


def _money_or_none(value: float | int | Decimal | None) -> float | None:
    if value is None or pd.isna(value) or float(value) == 0:
        return None
    return float(value)


def _charges_by_month(
    opportunities: pd.DataFrame,
    months: Iterable[date],
    *,
    states: set[str],
    statuses: set[str],
) -> pd.DataFrame:
    rows = []
    for month in months:
        month_ts = pd.Timestamp(month)
        matching = opportunities[
            (opportunities["month"] == month_ts)
            & (opportunities["state"].isin(states))
            & (opportunities["status"].isin(statuses))
        ]
        total = float(matching["charge_total"].sum()) if not matching.empty else 0
        rows.append(
            {
                "month": month,
                "month_name": month.strftime("%B %Y"),
                "total_charge": _money_or_none(total),
            }
        )
    return pd.DataFrame(rows)


def _confirmed_table(
    opportunities: pd.DataFrame, months: list[date], targets: dict[date, int]
) -> pd.DataFrame:
    df = _charges_by_month(
        opportunities,
        months,
        states={"Order"},
        statuses=set(CONFIRMED_STATUSES),
    )
    df["target"] = df["month"].map(targets)
    df["variance"] = df.apply(
        lambda row: None
        if pd.isna(row["target"]) or pd.isna(row["total_charge"])
        else float(row["total_charge"]) - float(row["target"]),
        axis=1,
    )
    return df[["month", "month_name", "total_charge", "target", "variance"]]


def _jobs_table(opportunities: pd.DataFrame, months: list[date], today: date) -> pd.DataFrame:
    rows = []
    orders = opportunities[
        (opportunities["state"] == "Order")
        & (opportunities["status"].isin(CONFIRMED_STATUSES))
    ]
    for month in months:
        same_month = orders[orders["starts_at"].dt.month == month.month]
        this_year_rows = same_month[same_month["starts_at"].dt.year == today.year]
        last_year_rows = same_month[same_month["starts_at"].dt.year == today.year - 1]
        this_year = this_year_rows["opportunity_id"].nunique()
        last_year = last_year_rows["opportunity_id"].nunique()
        last_year_revenue = (
            float(last_year_rows["charge_total"].sum()) if not last_year_rows.empty else 0
        )
        if last_year == 0:
            change_pct = 100.0 if this_year else 0.0
        else:
            change_pct = round(((this_year - last_year) / last_year) * 100, 1)
        rows.append(
            {
                "month_name": month.strftime("%B %Y"),
                "last_year_count": int(last_year),
                "last_year_revenue": _money_or_none(last_year_revenue),
                "this_year_count": int(this_year),
                "yoy_change_pct": change_pct,
            }
        )
    return pd.DataFrame(rows)


def _latest_sync_status(sync_runs: pd.DataFrame) -> dict[str, object | None]:
    if sync_runs.empty:
        return {
            "mode": None,
            "status": "never_run",
            "finished_at": None,
            "rows_fetched": None,
            "rows_upserted": None,
            "rows_deleted": None,
            "error": None,
        }
    row = sync_runs.iloc[0]
    return {
        "mode": row.get("mode"),
        "status": row.get("status"),
        "finished_at": row.get("finished_at"),
        "rows_fetched": row.get("rows_fetched"),
        "rows_upserted": row.get("rows_upserted"),
        "rows_deleted": row.get("rows_deleted"),
        "error": row.get("error"),
    }


def build_dashboard_snapshot(
    opportunities: pd.DataFrame,
    targets: pd.DataFrame,
    sync_runs: pd.DataFrame,
    *,
    now: datetime | None = None,
) -> DashboardSnapshot:
    now = now or datetime.now()
    today = now.date()
    opportunities = _normalise_opportunities(opportunities)
    latest_targets = select_latest_targets(targets)

    confirmed_months = month_window(today, 1)
    current_months = month_window(today, 0)
    confirmed = _confirmed_table(opportunities, confirmed_months, latest_targets)
    jobs = _jobs_table(opportunities, confirmed_months, today)
    reserved = _charges_by_month(
        opportunities,
        current_months,
        states={"Quotation"},
        statuses={"Reserved"},
    )
    quote = _charges_by_month(
        opportunities,
        current_months,
        states={"Quotation"},
        statuses={"Provisional"},
    )

    current_confirmed = _confirmed_table(
        opportunities, [_month_start(today)], latest_targets
    ).iloc[0]
    callouts = {
        "confirmed": current_confirmed["total_charge"],
        "target": current_confirmed["target"],
        "reserved": reserved.iloc[0]["total_charge"] if not reserved.empty else None,
        "quote": quote.iloc[0]["total_charge"] if not quote.empty else None,
    }

    return DashboardSnapshot(
        confirmed=confirmed,
        jobs=jobs,
        reserved=reserved,
        quote=quote,
        callouts=callouts,
        sync_status=_latest_sync_status(sync_runs),
        generated_at=now,
    )


def read_dashboard_snapshot(
    engine: Engine | None = None, *, now: datetime | None = None
) -> DashboardSnapshot:
    engine = engine or get_engine()
    ensure_schema(engine)
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            opportunities = pd.read_sql(
                text(
                    """
                    SELECT opportunity_id, subject, starts_at, organisation, state,
                           status, charge_total, updated_at
                    FROM opportunities
                    """
                ),
                conn,
            )
            targets = pd.read_sql(
                text("SELECT target_date, amount, created_at FROM targets"),
                conn,
            )
            sync_runs = pd.read_sql(
                text(
                    """
                    SELECT mode, status, finished_at, rows_fetched, rows_upserted,
                           rows_deleted, error
                    FROM sync_runs
                    ORDER BY finished_at DESC, id DESC
                    LIMIT 1
                    """
                ),
                conn,
            )
            trans.commit()
        except Exception:
            trans.rollback()
            raise
    return build_dashboard_snapshot(opportunities, targets, sync_runs, now=now)


def save_targets(targets: dict[date, int], engine: Engine | None = None) -> None:
    if not targets:
        return
    engine = engine or get_engine()
    ensure_schema(engine)
    with engine.begin() as conn:
        for target_date, amount in targets.items():
            if amount < 0:
                raise ValueError("Targets must be non-negative")
            conn.execute(
                text(
                    """
                    INSERT INTO targets (target_date, amount)
                    VALUES (:target_date, :amount)
                    """
                ),
                {"target_date": target_date, "amount": int(amount)},
            )


def data_is_stale(sync_status: dict[str, object | None], now: datetime | None = None) -> bool:
    now = now or datetime.now()
    finished_at = sync_status.get("finished_at")
    if finished_at is None or pd.isna(finished_at):
        return True
    finished_at = pd.to_datetime(finished_at).to_pydatetime()
    return now - finished_at > timedelta(hours=1)
