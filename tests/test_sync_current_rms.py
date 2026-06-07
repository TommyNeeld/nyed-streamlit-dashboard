from datetime import datetime

import pytest

from src import sync_current_rms
from src.sync_current_rms import (
    fetch_opportunities,
    fetch_opportunities_for_state,
    parse_current_rms_datetime,
    prepare_opportunity_record,
)


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return self.responses.pop(0)


def test_prepare_opportunity_record_handles_missing_member_and_dates():
    record = prepare_opportunity_record(
        {
            "id": 123,
            "subject": "Event",
            "starts_at": None,
            "updated_at": "2026-06-07T10:30:00.000Z",
            "member": None,
            "state_name": "Order",
            "status_name": "Completed",
            "charge_total": 0,
        }
    )

    assert record["opportunity_id"] == "123"
    assert record["organisation"] == ""
    assert record["starts_at"] is None
    assert record["updated_at"] == datetime(2026, 6, 7, 10, 30)
    assert record["charge_total"] == 0


def test_parse_current_rms_datetime_returns_none_for_empty_values():
    assert parse_current_rms_datetime(None) is None
    assert parse_current_rms_datetime("") is None


def test_fetch_opportunities_paginates_until_empty(monkeypatch):
    monkeypatch.setattr(
        sync_current_rms,
        "_headers",
        lambda: {"X-AUTH-TOKEN": "token", "X-SUBDOMAIN": "nyed"},
    )
    session = FakeSession(
        [
            FakeResponse(200, {"opportunities": [{"id": 1}]}),
            FakeResponse(200, {"opportunities": [{"id": 2}]}),
            FakeResponse(200, {"opportunities": []}),
        ]
    )

    rows = fetch_opportunities_for_state(
        session,
        "orders",
        updated_at_gt=datetime(2026, 6, 7, 10, 0, 0),
    )

    assert rows == [{"id": 1}, {"id": 2}]
    assert len(session.calls) == 3
    first_params = session.calls[0]["kwargs"]["params"]
    assert first_params["filtermode[]"] == ["orders", "not_cancelled"]
    assert first_params["q[updated_at_gt]"] == "2026-06-07T10:00:00.000Z"


def test_backfill_fetches_all_states_without_updated_at_filter(monkeypatch):
    monkeypatch.setattr(
        sync_current_rms,
        "_headers",
        lambda: {"X-AUTH-TOKEN": "token", "X-SUBDOMAIN": "nyed"},
    )
    session = FakeSession(
        [
            FakeResponse(200, {"opportunities": []}),
            FakeResponse(200, {"opportunities": []}),
            FakeResponse(200, {"opportunities": []}),
        ]
    )

    rows = fetch_opportunities(
        "backfill",
        updated_at_gt=datetime(2026, 6, 7, 10, 0, 0),
        session=session,
    )

    assert rows == []
    assert len(session.calls) == 3
    assert all("q[updated_at_gt]" not in call["kwargs"]["params"] for call in session.calls)


def test_fetch_opportunities_raises_on_api_error(monkeypatch):
    monkeypatch.setattr(sync_current_rms, "_headers", lambda: {})
    session = FakeSession([FakeResponse(500, text="nope")])

    with pytest.raises(RuntimeError, match="Current RMS returned 500"):
        fetch_opportunities_for_state(session, "quotations")
