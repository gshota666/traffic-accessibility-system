"""Small, offline helpers for the locked paper's explicitly defined metrics.

Only the threshold coverage rule is implemented here because the MP function
and parameters are intentionally not fixed.  These helpers do not read the
database or contact a map service.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Mapping, Sequence


def daily_then_equal_weight_threshold_coverage(
    observations: Iterable[Mapping],
    populations: Mapping[str, float],
    days: Sequence[str],
    thresholds: Sequence[float] = (60, 120, 180, 240),
) -> dict[float, float | None]:
    """Compute M thresholds by day first, then average the daily values.

    ``None`` is returned for a threshold when a required day is missing or a
    source population is missing.  Missing observations are not interpreted
    as unreachable and are never filled with zero.
    """
    day_rows: dict[str, list[Mapping]] = defaultdict(list)
    for row in observations:
        day = str(row.get("day") or "")
        if day in days:
            day_rows[day].append(row)
    if any(day not in day_rows for day in days):
        return {float(t): None for t in thresholds}
    expected_sources = {str(source_id) for source_id in populations}
    if any(len(day_rows[day]) != len(expected_sources) or {str(row.get("source_id")) for row in day_rows[day]} != expected_sources for day in days):
        return {float(t): None for t in thresholds}
    if any(str(row.get("source_id")) not in populations for row in (r for rows in day_rows.values() for r in rows)):
        return {float(t): None for t in thresholds}
    result: dict[float, float | None] = {}
    for threshold in thresholds:
        daily = []
        for day in days:
            covered = 0.0
            for row in day_rows[day]:
                duration = row.get("duration_min")
                if duration is not None and float(duration) <= float(threshold):
                    covered += float(populations[str(row["source_id"])])
            daily.append(covered)
        result[float(threshold)] = sum(daily) / len(daily)
    return result
