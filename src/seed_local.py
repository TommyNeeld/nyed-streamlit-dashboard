from __future__ import annotations

import argparse
from datetime import datetime

from sqlalchemy import text

from src.dashboard_data import ensure_schema, get_engine


SAMPLE_OPPORTUNITIES = [
    {
        "opportunity_id": "sample-order-current",
        "subject": "Sample current order",
        "starts_at": datetime(2026, 6, 12, 9, 0),
        "organisation": "Sample Client A",
        "state": "Order",
        "status": "Completed",
        "charge_total": 12500,
        "updated_at": datetime(2026, 6, 7, 9, 0),
    },
    {
        "opportunity_id": "sample-order-prior",
        "subject": "Sample prior-year order",
        "starts_at": datetime(2025, 6, 14, 9, 0),
        "organisation": "Sample Client B",
        "state": "Order",
        "status": "Completed",
        "charge_total": 8400,
        "updated_at": datetime(2025, 6, 15, 9, 0),
    },
    {
        "opportunity_id": "sample-reserved-current",
        "subject": "Sample reserved quotation",
        "starts_at": datetime(2026, 6, 18, 9, 0),
        "organisation": "Sample Client C",
        "state": "Quotation",
        "status": "Reserved",
        "charge_total": 3200,
        "updated_at": datetime(2026, 6, 7, 9, 0),
    },
    {
        "opportunity_id": "sample-quote-current",
        "subject": "Sample provisional quotation",
        "starts_at": datetime(2026, 6, 22, 9, 0),
        "organisation": "Sample Client D",
        "state": "Quotation",
        "status": "Provisional",
        "charge_total": 5100,
        "updated_at": datetime(2026, 6, 7, 9, 0),
    },
]


def seed_if_empty(*, force: bool = False) -> None:
    engine = get_engine()
    ensure_schema(engine)
    with engine.begin() as conn:
        opportunity_count = conn.execute(
            text("SELECT COUNT(*) FROM opportunities")
        ).scalar_one()
        if force:
            conn.execute(text("DELETE FROM opportunities"))
            conn.execute(text("DELETE FROM api_reads"))
            conn.execute(text("DELETE FROM sync_runs"))
            opportunity_count = 0

        if opportunity_count == 0:
            conn.execute(
                text(
                    """
                    INSERT INTO opportunities (
                        opportunity_id, subject, starts_at, organisation, state,
                        status, charge_total, updated_at
                    ) VALUES (
                        :opportunity_id, :subject, :starts_at, :organisation,
                        :state, :status, :charge_total, :updated_at
                    )
                    """
                ),
                SAMPLE_OPPORTUNITIES,
            )

        api_read_count = conn.execute(text("SELECT COUNT(*) FROM api_reads")).scalar_one()
        if api_read_count == 0:
            conn.execute(
                text("INSERT INTO api_reads (time_of_read) VALUES (:time_of_read)"),
                {"time_of_read": datetime(2026, 6, 7, 9, 0)},
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load synthetic local seed data")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace existing local opportunities/api_reads seed data",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    seed_if_empty(force=args.force)
    print("Synthetic local seed data loaded if tables were empty.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
