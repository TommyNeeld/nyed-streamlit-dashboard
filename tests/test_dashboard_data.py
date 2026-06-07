from datetime import datetime

import pandas as pd

from src.dashboard_data import (
    build_dashboard_snapshot,
    data_is_stale,
    month_window,
    select_latest_targets,
)
from src.sync_current_rms import ids_to_delete_for_full_sync


def test_month_window_includes_previous_current_and_two_future_months():
    assert [month.isoformat() for month in month_window(datetime(2026, 6, 7).date(), 1)] == [
        "2026-05-01",
        "2026-06-01",
        "2026-07-01",
        "2026-08-01",
    ]


def test_select_latest_targets_uses_newest_created_at_per_month():
    targets = pd.DataFrame(
        [
            {"target_date": "2026-06-01", "amount": 1000, "created_at": "2026-01-01"},
            {"target_date": "2026-06-01", "amount": 2500, "created_at": "2026-01-02"},
            {"target_date": "2026-07-01", "amount": 3000, "created_at": "2026-01-01"},
        ]
    )

    latest = select_latest_targets(targets)

    assert latest[datetime(2026, 6, 1).date()] == 2500
    assert latest[datetime(2026, 7, 1).date()] == 3000


def test_build_dashboard_snapshot_matches_sales_rules():
    opportunities = pd.DataFrame(
        [
            {
                "opportunity_id": "1",
                "starts_at": "2026-06-10",
                "state": "Order",
                "status": "Completed",
                "charge_total": 1000,
            },
            {
                "opportunity_id": "2",
                "starts_at": "2026-06-11",
                "state": "Order",
                "status": "Reserved",
                "charge_total": 9999,
            },
            {
                "opportunity_id": "3",
                "starts_at": "2026-06-12",
                "state": "Quotation",
                "status": "Reserved",
                "charge_total": 400,
            },
            {
                "opportunity_id": "4",
                "starts_at": "2026-06-13",
                "state": "Quotation",
                "status": "Provisional",
                "charge_total": 600,
            },
            {
                "opportunity_id": "5",
                "starts_at": "2025-06-10",
                "state": "Order",
                "status": "Completed",
                "charge_total": 100,
            },
        ]
    )
    targets = pd.DataFrame(
        [{"target_date": "2026-06-01", "amount": 1500, "created_at": "2026-01-01"}]
    )
    sync_runs = pd.DataFrame(
        [
            {
                "mode": "incremental",
                "status": "success",
                "finished_at": "2026-06-07 10:00:00",
                "rows_fetched": 4,
                "rows_upserted": 4,
                "rows_deleted": 0,
                "error": None,
            }
        ]
    )

    snapshot = build_dashboard_snapshot(
        opportunities,
        targets,
        sync_runs,
        now=datetime(2026, 6, 7, 10, 5, 0),
    )

    june_confirmed = snapshot.confirmed[snapshot.confirmed["month_name"] == "June 2026"].iloc[0]
    june_jobs = snapshot.jobs[snapshot.jobs["month_name"] == "June 2026"].iloc[0]

    assert june_confirmed["total_charge"] == 1000
    assert june_confirmed["target"] == 1500
    assert june_confirmed["variance"] == -500
    assert snapshot.callouts["reserved"] == 400
    assert snapshot.callouts["quote"] == 600
    assert june_jobs["this_year_count"] == 1
    assert june_jobs["last_year_count"] == 1
    assert june_jobs["last_year_revenue"] == 100
    assert june_jobs["yoy_change_pct"] == 0


def test_data_is_stale_after_one_hour():
    assert data_is_stale(
        {"finished_at": "2026-06-07 08:59:00"},
        now=datetime(2026, 6, 7, 10, 0, 0),
    )
    assert not data_is_stale(
        {"finished_at": "2026-06-07 09:30:00"},
        now=datetime(2026, 6, 7, 10, 0, 0),
    )


def test_full_sync_stale_id_detection():
    assert ids_to_delete_for_full_sync(["1", "2", "3"], ["2", "3", "4"]) == ["1"]
