from __future__ import annotations

import base64
import hmac
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

import extra_streamlit_components as stx
import pandas as pd
import streamlit as st

from src.config import get_secret
from src.dashboard_data import (
    DashboardSnapshot,
    data_is_stale,
    get_engine,
    read_dashboard_snapshot,
    save_targets,
)

st.set_page_config(
    page_title="NYED Sales Dashboard",
    page_icon="src/assets/favicon.ico",
    layout="wide",
)

AUTH_COOKIE_NAME = "nyed_dashboard_auth"
AUTH_COOKIE_DAYS = 30


@st.cache_resource
def cached_engine():
    return get_engine()


def cookie_manager():
    return stx.CookieManager(key="nyed_cookie_manager")


def load_snapshot() -> DashboardSnapshot:
    return read_dashboard_snapshot(cached_engine())


def auth_credentials() -> tuple[str, str | None]:
    return (
        get_secret("DASHBOARD_USERNAME", "admin") or "admin",
        get_secret("DASHBOARD_PASSWORD"),
    )


def auth_cookie_value() -> str | None:
    username, password = auth_credentials()
    if not password:
        return None
    digest = hmac.new(password.encode("utf-8"), username.encode("utf-8"), "sha256")
    return f"{username}:{digest.hexdigest()}"


def set_auth_cookie() -> None:
    value = auth_cookie_value()
    if not value:
        return
    cookie_manager().set(
        AUTH_COOKIE_NAME,
        value,
        expires_at=datetime.utcnow() + timedelta(days=AUTH_COOKIE_DAYS),
    )


def clear_auth_cookie() -> None:
    cookie_manager().delete(AUTH_COOKIE_NAME)


def has_valid_auth_cookie() -> bool:
    expected = auth_cookie_value()
    if not expected:
        return False
    actual = cookie_manager().get(AUTH_COOKIE_NAME)
    return bool(actual) and hmac.compare_digest(actual, expected)


def authenticate(username: str, password: str) -> bool:
    expected_username, expected_password = auth_credentials()
    if not expected_password:
        return False
    return hmac.compare_digest(username, expected_username) and hmac.compare_digest(
        password,
        expected_password,
    )


def is_authenticated() -> bool:
    if st.session_state.get("authenticated"):
        return True
    if has_valid_auth_cookie():
        st.session_state["authenticated"] = True
        return True
    return False


def render_login() -> None:
    _, configured_password = auth_credentials()
    if not configured_password:
        st.error("Dashboard password is not configured.")
        st.stop()

    st.markdown('<div class="login-panel">', unsafe_allow_html=True)
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")
    st.markdown("</div>", unsafe_allow_html=True)

    if submitted:
        if authenticate(username, password):
            st.session_state["authenticated"] = True
            set_auth_cookie()
            st.rerun()
        st.error("Invalid username or password.")


def render_logout() -> None:
    with st.sidebar:
        if st.button("Sign out"):
            st.session_state["authenticated"] = False
            clear_auth_cookie()
            st.rerun()


def format_currency(value: float | int | None, compact: bool = False) -> str:
    if value is None or pd.isna(value):
        return ""
    if compact:
        return f"£{float(value) / 1000:,.1f}K"
    return f"£{float(value):,.0f}"


def format_number(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value):,.1f}"


def render_logo() -> None:
    logo_path = Path("src/assets/images/nyed-logo-b.svg")
    if logo_path.exists():
        logo_data = base64.b64encode(logo_path.read_bytes()).decode("ascii")
        st.markdown(
            f"""
            <div class="dashboard-logo">
              <img src="data:image/svg+xml;base64,{logo_data}" alt="Not Your Every Day Furniture Hire">
            </div>
            """,
            unsafe_allow_html=True,
        )


def metric_html(label: str, value: str, color: str = "#111827") -> None:
    st.markdown(
        f"""
        <div class="metric-block">
          <div class="metric-label">{label}</div>
          <div class="metric-value" style="color:{color};">{value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_sync_health(snapshot: DashboardSnapshot) -> None:
    status = snapshot.sync_status
    finished_at = status.get("finished_at")
    if finished_at is None or pd.isna(finished_at):
        st.markdown(
            '<p class="sync-note sync-warning">Data has not synced yet.</p>',
            unsafe_allow_html=True,
        )
        return

    finished_at = pd.to_datetime(finished_at)
    label = (
        f"Last successful sync: {finished_at:%d-%m-%Y %H:%M:%S} "
        f"({status.get('mode')}, fetched {status.get('rows_fetched')}, "
        f"upserted {status.get('rows_upserted')}, deleted {status.get('rows_deleted')})"
    )
    if status.get("status") != "success":
        st.markdown(
            f'<p class="sync-note sync-error">Latest sync failed: {escape(str(status.get("error")))}</p>',
            unsafe_allow_html=True,
        )
    elif data_is_stale(status, snapshot.generated_at):
        st.markdown(
            f'<p class="sync-note sync-warning">{escape(label)}. Data is more than one hour old.</p>',
            unsafe_allow_html=True,
        )


def render_callouts(snapshot: DashboardSnapshot) -> None:
    logo_col, confirmed_col, reserved_col, quote_col = st.columns([1.7, 1, 1, 1])
    with logo_col:
        render_logo()
    confirmed = snapshot.callouts.get("confirmed")
    target = snapshot.callouts.get("target")
    confirmed_color = "#111827"
    if target is not None and not pd.isna(target) and confirmed is not None:
        confirmed_color = "#2E8B57" if confirmed >= target else "#DC143C"
    with confirmed_col:
        metric_html(
            "Confirmed Sales",
            format_currency(confirmed, compact=True) or "--",
            confirmed_color,
        )
    with reserved_col:
        metric_html(
            "Reserved",
            format_currency(snapshot.callouts.get("reserved"), compact=True) or "--",
        )
    with quote_col:
        metric_html(
            "On Quote",
            format_currency(snapshot.callouts.get("quote"), compact=True) or "--",
        )


def section_title(title: str) -> None:
    st.markdown(f'<h2 class="section-title">{escape(title)}</h2>', unsafe_allow_html=True)


def _cell_value(value, *, currency: bool = False, signed: bool = False) -> str:
    if currency:
        return format_currency(value)
    if value is None or pd.isna(value):
        return ""
    if signed:
        return f"{float(value):+.1f}"
    return str(int(value)) if isinstance(value, (int, float)) and float(value).is_integer() else str(value)


def render_html_table(
    df: pd.DataFrame,
    columns: list[tuple[str, str]],
    *,
    currency_columns: set[str] | None = None,
    signed_columns: set[str] | None = None,
    bar_column: str | None = None,
) -> None:
    currency_columns = currency_columns or set()
    signed_columns = signed_columns or set()
    header = "".join(f"<th>{escape(label)}</th>" for _, label in columns)
    rows = []
    for _, row in df.iterrows():
        cells = []
        for column, _ in columns:
            value = row.get(column)
            text = _cell_value(
                value,
                currency=column in currency_columns,
                signed=column in signed_columns,
            )
            classes = ["numeric"] if column != "month_name" else []
            style = ""
            if column == bar_column and value is not None and not pd.isna(value):
                width = min(abs(float(value)), 100)
                color = "#8BEA86" if float(value) >= 0 else "#FF6B72"
                style = (
                    "background:"
                    f"linear-gradient(90deg, {color} 0%, {color} {width}%, "
                    f"transparent {width}%, transparent 100%);"
                )
            class_attr = f' class="{" ".join(classes)}"' if classes else ""
            style_attr = f' style="{style}"' if style else ""
            cells.append(f"<td{class_attr}{style_attr}>{escape(text)}</td>")
        rows.append(f"<tr>{''.join(cells)}</tr>")
    st.markdown(
        f"""
        <table class="sales-table">
          <thead><tr>{header}</tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
        """,
        unsafe_allow_html=True,
    )


def render_tables(snapshot: DashboardSnapshot) -> None:
    confirmed = snapshot.confirmed.drop(columns=["month"], errors="ignore").copy()
    reserved = snapshot.reserved.drop(columns=["month"], errors="ignore").copy()
    quote = snapshot.quote.drop(columns=["month"], errors="ignore").copy()

    left, right = st.columns(2)
    with left:
        section_title("CONFIRMED")
        render_html_table(
            confirmed,
            [
                ("month_name", "Month"),
                ("total_charge", "Total Charge"),
                ("target", "Target"),
                ("variance", "Variance"),
            ],
            currency_columns={"total_charge", "target", "variance"},
        )
    with right:
        section_title("MONTHLY JOB COUNTS")
        render_html_table(
            snapshot.jobs,
            [
                ("month_name", "Month"),
                ("last_year_count", "Last Year"),
                ("last_year_revenue", "Prior Yr Rev."),
                ("this_year_count", "This Year"),
                ("yoy_change_pct", "Change %"),
            ],
            currency_columns={"last_year_revenue"},
            signed_columns={"yoy_change_pct"},
            bar_column="yoy_change_pct",
        )

    left, right = st.columns(2)
    with left:
        section_title("RESERVED")
        render_html_table(
            reserved,
            [("month_name", "Month"), ("total_charge", "Total Charge")],
            currency_columns={"total_charge"},
        )
    with right:
        section_title("ON QUOTE")
        render_html_table(
            quote,
            [("month_name", "Month"), ("total_charge", "Total Charge")],
            currency_columns={"total_charge"},
        )


def render_target_editor(snapshot: DashboardSnapshot) -> None:
    with st.expander("Set Monthly Targets"):
        target_rows = snapshot.confirmed[["month", "month_name", "target"]].copy()
        target_rows["target"] = target_rows["target"].fillna(0).astype(int)
        edited = st.data_editor(
            target_rows[["month_name", "target"]],
            hide_index=True,
            width="stretch",
            column_config={
                "month_name": st.column_config.TextColumn("Month", disabled=True),
                "target": st.column_config.NumberColumn("Target", min_value=0, step=1),
            },
        )
        if st.button("Save Targets", type="primary"):
            payload: dict[date, int] = {}
            for month, amount in zip(target_rows["month"], edited["target"]):
                if pd.notna(amount):
                    payload[pd.to_datetime(month).date()] = int(amount)
            save_targets(payload, cached_engine())
            st.success("Targets saved successfully.")
            st.rerun()


st.markdown(
    """
    <style>
      @font-face {
        font-family: "Gilroy-Regular";
        src: url("app/static/src/assets/fonts/Gilroy-Regular.ttf") format("truetype");
      }
      :root { color-scheme: light; }
      .stApp {
        background: #f3f4f6;
        color: #20242b;
        font-family: "Gilroy-Regular", "Avenir Next", "Helvetica Neue", Arial, sans-serif;
      }
      .main .block-container,
      div[data-testid="stMainBlockContainer"] {
        max-width: 1850px;
        padding: 1.15rem 2.25rem 2.75rem;
        background: linear-gradient(to bottom, #f3f4f6 0 120px, #ffffff 120px 100%);
      }
      section[data-testid="stMain"] {
        background: #f3f4f6;
      }
      h1 {
        text-align: center;
        font-size: 2.8rem !important;
        font-weight: 400 !important;
        color: #20242b;
        margin: 0 0 0.3rem !important;
      }
      .dashboard-caption {
        color: #7a7a7a;
        font-size: 1.1rem;
        text-align: center;
        margin-bottom: 1.15rem;
      }
      .sync-note {
        color: #777;
        font-size: 0.78rem;
        text-align: center;
        margin: -0.55rem 0 0.45rem;
      }
      .sync-warning { color: #8a6d00; }
      .sync-error { color: #b42318; }
      .login-panel {
        max-width: 360px;
        margin: 2.2rem auto 0;
      }
      div[data-testid="stForm"] {
        max-width: 360px;
        margin: 2.2rem auto 0;
      }
      .metric-block {
        background: transparent;
        border: 0;
        border-radius: 0;
        padding: 0;
        min-height: 126px;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        text-align: center;
      }
      .metric-label {
        color: #7a7a7a;
        font-size: 1.05rem;
        margin-bottom: 0.15rem;
      }
      .metric-value {
        color: #20242b;
        font-size: 2.35rem;
        font-weight: 600;
        line-height: 1.15;
      }
      h2.section-title,
      .section-title {
        text-align: center;
        font-size: 1.05rem !important;
        font-weight: 400 !important;
        color: #20242b;
        margin: 0.9rem 0 0.65rem !important;
        line-height: 1.2 !important;
      }
      .sales-table {
        width: 100%;
        border-collapse: collapse;
        background: #ffffff;
        color: #20242b;
        font-size: 1.05rem;
        table-layout: fixed;
      }
      .sales-table th,
      .sales-table td {
        border: 1px solid #d3d3d3;
        padding: 0.72rem 0.9rem;
        vertical-align: middle;
      }
      .sales-table th {
        background: #e6e6e6;
        font-weight: 400;
        text-align: left;
      }
      .sales-table td.numeric,
      .sales-table th:not(:first-child) {
        text-align: right;
      }
      .sales-table tbody tr:nth-child(even) td {
        background-color: #fafafa;
      }
      div[data-testid="stImage"] {
        display: flex;
        justify-content: center;
        align-items: center;
      }
      div[data-testid="stImage"] img {
        display: block;
        margin: 0.35rem auto 0;
      }
      .dashboard-logo {
        display: flex;
        justify-content: center;
        align-items: center;
        width: 100%;
      }
      .dashboard-logo img {
        display: block;
        width: 300px;
        max-width: 100%;
        height: auto;
        margin: 0.35rem auto 0;
      }
      div[data-testid="stExpander"] {
        background: #ffffff;
        border: 1px solid #d8dde3;
        border-radius: 6px;
        margin-top: 1.7rem;
      }
      div[data-testid="stExpander"] summary {
        font-size: 1rem;
      }
      header[data-testid="stHeader"] {
        background: transparent;
      }
      header[data-testid="stHeader"],
      div[data-testid="stToolbar"],
      div[data-testid="stDecoration"],
      div[data-testid="stStatusWidget"] {
        display: none;
      }
      div[data-testid="stHorizontalBlock"] {
        gap: 1rem;
      }
      @media (min-width: 1200px) {
        .main .block-container {
          padding-left: 4rem;
          padding-right: 4rem;
        }
      }
      @media (max-width: 900px) {
        .main .block-container,
        div[data-testid="stMainBlockContainer"] {
          padding: 0.65rem 0.8rem 1.6rem;
          background: linear-gradient(to bottom, #f3f4f6 0 100px, #ffffff 100px 100%);
        }
        h1 {
          font-size: 1.65rem !important;
        }
        .dashboard-caption {
          font-size: 0.82rem;
          margin-bottom: 0.8rem;
        }
        .sync-note {
          font-size: 0.72rem;
          line-height: 1.35;
          margin: -0.35rem 0 0.4rem;
        }
        .metric-block {
          padding: 0;
          min-height: auto;
          text-align: center;
          align-items: center;
        }
        .metric-label {
          font-size: 0.9rem;
        }
        .metric-value {
          font-size: 1.75rem;
        }
        div[data-testid="stImage"] {
          justify-content: center;
          width: 100%;
        }
        div[data-testid="stImage"] img {
          max-width: 74%;
          margin: 0.15rem auto 0.4rem;
        }
        .dashboard-logo {
          width: 100%;
          justify-content: center;
        }
        .dashboard-logo img {
          width: min(300px, 74vw);
          max-width: 100%;
          margin: 0.15rem auto 0.4rem;
        }
        h2.section-title,
        .section-title {
          font-size: 0.95rem !important;
          margin: 0.9rem 0 0.45rem !important;
        }
        .sales-table {
          font-size: 0.82rem;
        }
        .sales-table th,
        .sales-table td {
          padding: 0.45rem 0.5rem;
        }
      }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown("<h1>SALES DASHBOARD</h1>", unsafe_allow_html=True)


if not is_authenticated():
    render_login()
    st.stop()

render_logout()


@st.fragment(run_every="5min")
def render_dashboard() -> None:
    snapshot = load_snapshot()
    st.markdown(
        f'<p class="dashboard-caption">Last updated: {snapshot.generated_at:%d-%m-%Y %H:%M:%S}</p>',
        unsafe_allow_html=True,
    )
    render_sync_health(snapshot)
    render_callouts(snapshot)
    render_tables(snapshot)
    render_target_editor(snapshot)


render_dashboard()
