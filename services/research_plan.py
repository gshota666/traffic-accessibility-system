"""Versioned research-calendar rules.

The final paper plan is intentionally kept separate from the historical
12-day template.  This module contains only offline, deterministic metadata;
it never calls a map service.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

FINAL_TEMPLATE_VERSION = "final-2026-7day-10am-v1"
FINAL_PLAN = (
    {"phase": "国庆D1", "day_type": "holiday", "date": "2026-10-01", "times": ("10:00",), "directions": ("outbound",), "note": "国庆D1，固定10:00，仅客源地→重点村"},
    {"phase": "国庆D2", "day_type": "holiday", "date": "2026-10-02", "times": ("10:00",), "directions": ("outbound",), "note": "国庆D2，固定10:00，仅客源地→重点村"},
    {"phase": "国庆D3", "day_type": "holiday", "date": "2026-10-03", "times": ("10:00",), "directions": ("outbound",), "note": "国庆D3，固定10:00，仅客源地→重点村"},
    {"phase": "普通周末1·周六", "day_type": "weekend", "date": "2026-10-17", "times": ("10:00",), "directions": ("outbound",), "note": "普通周末1·周六，固定10:00，仅客源地→重点村"},
    {"phase": "普通周末1·周日", "day_type": "weekend", "date": "2026-10-18", "times": ("10:00",), "directions": ("outbound",), "note": "普通周末1·周日，固定10:00，仅客源地→重点村"},
    {"phase": "普通周末2·周六", "day_type": "weekend", "date": "2026-10-24", "times": ("10:00",), "directions": ("outbound",), "note": "普通周末2·周六，固定10:00，仅客源地→重点村"},
    {"phase": "普通周末2·周日", "day_type": "weekend", "date": "2026-10-25", "times": ("10:00",), "directions": ("outbound",), "note": "普通周末2·周日，固定10:00，仅客源地→重点村"},
)


def final_plan_rows() -> list[dict[str, Any]]:
    """Return mutable UI/API rows without exposing the module constants."""
    return [
        {**row, "times": list(row["times"]), "directions": list(row["directions"]), "window_minutes": 60, "notes": row["note"]}
        for row in FINAL_PLAN
    ]


def _normalise_row(row: Any) -> tuple[Any, ...]:
    def get(key: str, default: Any = None):
        if isinstance(row, dict):
            return row.get(key, default)
        return getattr(row, key, default)

    day = get("date")
    if isinstance(day, date):
        day = day.isoformat()
    return (
        str(day or ""),
        str(get("phase", "") or ""),
        str(get("day_type", "custom") or "custom"),
        tuple(sorted(set(get("times", ()) or ()) )),
        tuple(get("directions", ()) or ()),
    )


def is_final_plan(schedule: Iterable[Any]) -> bool:
    """Validate the locked calendar, deliberately ignoring window minutes.

    The Word fixes 10:00 but does not fix an allowed request window.  A
    provisional window must therefore never turn into part of the locked
    template identity.
    """
    rows = list(schedule or [])
    expected = [_normalise_row(row) for row in final_plan_rows()]
    return len(rows) == len(expected) and [_normalise_row(row) for row in rows] == expected


def final_plan_observation_count(source_count: int, destination_count: int) -> int:
    return int(source_count) * int(destination_count) * len(FINAL_PLAN)
