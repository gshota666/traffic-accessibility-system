"""Offline checks for the locked seven-day paper calendar."""

from datetime import datetime, timezone
from types import SimpleNamespace

import backend.app as app_module
from backend.app import create_app
from services.research_plan import FINAL_TEMPLATE_VERSION, final_plan_observation_count, final_plan_rows, is_final_plan
from services.paper_metrics import daily_then_equal_weight_threshold_coverage
from fastapi.testclient import TestClient


def _client(tmp_path):
    app = create_app(tmp_path / "data", token="final-plan", run_worker=False)
    return app, TestClient(app, headers={"x-session-token": "final-plan"})


def test_locked_calendar_and_count():
    rows = final_plan_rows()
    assert len(rows) == 7
    assert [row["date"] for row in rows] == [
        "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-17",
        "2026-10-18", "2026-10-24", "2026-10-25",
    ]
    assert all(row["times"] == ["10:00"] for row in rows)
    assert all(row["directions"] == ["outbound"] for row in rows)
    assert is_final_plan(rows)
    assert final_plan_observation_count(31, 94) == 20398
    changed = [dict(row, times=["11:00"]) if row["date"] == "2026-10-01" else row for row in rows]
    assert not is_final_plan(changed)


def test_threshold_coverage_is_daily_then_equal_weighted():
    observations = [
        {"day": "WE1", "source_id": "O1", "duration_min": 59},
        {"day": "WE1", "source_id": "O2", "duration_min": 61},
        {"day": "WE2", "source_id": "O1", "duration_min": 61},
        {"day": "WE2", "source_id": "O2", "duration_min": 59},
        {"day": "WE3", "source_id": "O1", "duration_min": 59},
        {"day": "WE3", "source_id": "O2", "duration_min": 61},
        {"day": "WE4", "source_id": "O1", "duration_min": 61},
        {"day": "WE4", "source_id": "O2", "duration_min": 59},
    ]
    daily = daily_then_equal_weight_threshold_coverage(observations, {"O1": 100, "O2": 200}, ["WE1", "WE2", "WE3", "WE4"], thresholds=(60,))
    assert daily[60.0] == 150.0  # (100+200+100+200)/4
    # Averaging route times first would give 60 minutes for both origins and
    # incorrectly count all 300 people; the locked rule must not do that.
    assert daily[60.0] != 300.0


def test_final_calendar_snapshot_and_mismatch_rejected(tmp_path, monkeypatch):
    # Keep the fixed 2026 template future-dated for this test, regardless of
    # when the suite is run.  This test checks calendar validation, not expiry.
    fixed_now = datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(app_module, "time", SimpleNamespace(time=lambda: fixed_now))
    app, client = _client(tmp_path)
    # A tiny valid import keeps the test offline and avoids creating a real
    # Amap task.  The final-calendar validator is independent of sample size.
    source_lines = ["ID,Name,lon,lat"] + [f"O{i:02d},Origin{i:02d},{114.3 + i / 1000:.6f},{30.5 + i / 1000:.6f}" for i in range(1, 32)]
    payload = ("\n".join(source_lines) + "\n").encode()
    uploaded = client.post("/api/imports", files={"file": ("source.csv", payload)}).json()
    checked = client.post(f"/api/imports/{uploaded['id']}/validate", json={"mapping": uploaded["mapping"], "crs": "GCJ-02"})
    assert checked.status_code == 200
    target_ids = []
    for i in range(1, 95):
        target = client.post("/api/targets", json={"name": f"Village{i:03d}", "lon": 114.4 + i / 1000, "lat": 30.6 + i / 1000, "crs": "GCJ-02", "target_id": f"D{i:03d}"})
        assert target.status_code == 200, target.text
        target_ids.append(target.json()["id"])
    body = {
        "name": "最终方案离线快照",
        "dataset_id": uploaded["id"],
        "target_ids": target_ids,
        "provider": "mock",
        "collection_mode": "scheduled",
        "schedule": final_plan_rows(),
        "schedule_template": FINAL_TEMPLATE_VERSION,
        "directions": ["outbound"],
        "estimate_seconds": 0.1,
    }
    created = client.post("/api/jobs", json=body)
    assert created.status_code == 200, created.text
    job = created.json()
    assert job["config"]["schedule_template"] == FINAL_TEMPLATE_VERSION
    assert job["total"] == 31 * 94 * 7
    assert job["config"]["schedule_window_confirmed"] is False

    bad = dict(body, schedule=[dict(row, times=["11:00"]) if row["date"] == "2026-10-01" else row for row in final_plan_rows()])
    rejected = client.post("/api/jobs", json=bad)
    assert rejected.status_code == 400
    assert "最终锁定方案" in rejected.json()["detail"]
