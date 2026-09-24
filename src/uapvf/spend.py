"""Compute spend tracking and monthly cap enforcement (FR-016, FR-023).

Configured analysis allocations (vision lineages and the narrative seed)
are tracked. Local battery adapters cost $0.00. Benchmark seed cases
(buyer_ref = '__benchmark_seed__') are exempt from the cap. In-progress
cases are never interrupted by the cap; the next queued case is capped
instead.
"""
from __future__ import annotations

import sqlite3
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timezone
from typing import Optional

from uapvf.config import BENCHMARK_BUYER_REF, Settings

USD_QUANTUM = Decimal("0.000001")


def _usd(value) -> Decimal:
    """Normalize money to exact micro-dollar precision for comparisons."""
    try:
        return Decimal(str(value)).quantize(USD_QUANTUM, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid USD amount: {value!r}") from exc


def record_spend(
    conn: sqlite3.Connection,
    case_id: Optional[str],
    cost_usd: float,
    description: str,
    recorded_at: Optional[str] = None,
) -> None:
    from uapvf.config import utcnow_iso

    conn.execute(
        "INSERT INTO spend_entries (case_id, cost_usd, description, recorded_at) "
        "VALUES (?, ?, ?, ?)",
        (case_id, float(_usd(cost_usd)), description, recorded_at or utcnow_iso()),
    )
    conn.commit()


def current_month(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m")


def month_total_usd(conn: sqlite3.Connection, month: Optional[str] = None) -> float:
    month = month or current_month()
    rows = conn.execute(
        "SELECT cost_usd FROM spend_entries "
        "WHERE substr(recorded_at, 1, 7) = ?",
        (month,),
    ).fetchall()
    return float(sum((_usd(row["cost_usd"]) for row in rows), Decimal("0")))


def is_benchmark_case(buyer_ref: Optional[str]) -> bool:
    return buyer_ref == BENCHMARK_BUYER_REF


def spend_allows_case(
    conn: sqlite3.Connection,
    settings: Settings,
    estimated_cost_usd: float,
    buyer_ref: Optional[str] = None,
) -> bool:
    """True if monthly total + estimate <= cap (equal is allowed, AC-022)."""
    if is_benchmark_case(buyer_ref):
        return True
    total = _usd(month_total_usd(conn))
    return (total + _usd(estimated_cost_usd)) <= _usd(settings.UAPV_SPEND_CAP_USD)


def spend_summary(conn: sqlite3.Connection, settings: Settings) -> dict:
    month = current_month()
    month_total = month_total_usd(conn, month)
    all_time_rows = conn.execute("SELECT cost_usd FROM spend_entries").fetchall()
    all_time = sum(
        (_usd(row["cost_usd"]) for row in all_time_rows), Decimal("0")
    )
    entries = conn.execute("SELECT COUNT(*) AS n FROM spend_entries").fetchone()["n"]
    pay = {}
    for status in ("unpaid", "paid", "comped"):
        pay[status] = conn.execute(
            "SELECT COUNT(*) AS n FROM cases WHERE payment_status = ?", (status,)
        ).fetchone()["n"]
    return {
        "month": month,
        "month_total_usd": round(month_total, 6),
        "all_time_usd": round(float(all_time), 6),
        "entries_count": entries,
        "spend_cap_usd": float(settings.UAPV_SPEND_CAP_USD),
        "payment_summary": pay,
        "estimated_paid_revenue_usd": pay.get("paid", 0) * 50.0,
    }
