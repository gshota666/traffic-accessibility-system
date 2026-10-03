"""Offline wall-clock throughput benchmark for the bounded worker.

This script never opens a network connection and never reads a production
database or API key.  It runs a small, isolated SQLite/file-persistence task
against an in-process fake provider, then reports the 2,914-row theoretical
limits separately from the constructed local benchmark.
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from backend.app import create_app
from providers.amap import AmapProvider
from providers.base import RouteResult
from services.settings import Settings


def run(sample_routes: int, latency: float, qps: float, concurrency: int):
    with tempfile.TemporaryDirectory(prefix='traffic-throughput-benchmark-') as tmp:
        app = create_app(Path(tmp), token='offline-benchmark', run_worker=False)
        with TestClient(app, headers={'x-session-token': 'offline-benchmark'}) as client:
            # The worker's real key lookup is replaced only inside this
            # isolated process.  No value is printed or persisted.
            Settings.key = lambda _self: 'offline-only-placeholder'
            app.state.worker.settings.save(dict(provider='amap', qps=qps,
                max_concurrency=concurrency, daily_limit=sample_routes + 10,
                retries=0, strategy='32'))

            def fake_route(self, origin, destination, *args):
                time.sleep(latency)
                return RouteResult(1234, 321, 'current_estimate', 'amap', '10000',
                                   raw_response={'status': '1', 'offline': True})

            original_route = AmapProvider.route
            AmapProvider.route = fake_route
            try:
                source = client.post('/api/imports', files={
                    'file': ('benchmark.csv', b'ID,Name,lon,lat\nS01,Source,114.3,30.5\n')
                }).json()
                checked = client.post(
                    f"/api/imports/{source['id']}/validate",
                    json={'mapping': source['mapping'], 'crs': 'WGS84'},
                )
                assert checked.status_code == 200, checked.text
                db = app.state.db
                target_ids = []
                with db.connect() as conn:
                    for i in range(sample_routes):
                        target_ids.append(conn.execute(
                            'INSERT INTO targets(target_id,name,lon,lat,crs,note) VALUES(?,?,?,?,?,?)',
                            (f'B{i:04d}', f'离线目标{i}', 114.31 + i / 10000, 30.51 + i / 10000, 'WGS84', 'offline benchmark'),
                        ).lastrowid)
                created = client.post('/api/jobs', json={
                    'name': '离线吞吐基准（不联网）', 'dataset_id': source['id'],
                    'target_ids': target_ids, 'provider': 'amap', 'date': '2099-01-01',
                    'times': ['10:00'], 'estimate_seconds': latency,
                    'acknowledge_capacity': True, 'acknowledge_estimate': True,
                })
                assert created.status_code == 200, created.text
                jid = created.json()['id']
                db.execute("UPDATE jobs SET status='running' WHERE id=?", (jid,))
                started = time.perf_counter()
                app.state.worker.run(db.one('SELECT * FROM jobs WHERE id=?', (jid,)))
                elapsed = time.perf_counter() - started
                attempts = db.rows('SELECT * FROM request_attempts WHERE job_id=?', (jid,))
                done = client.get(f'/api/jobs/{jid}').json()
                def avg(field):
                    vals = [float(r[field]) for r in attempts if r[field] is not None]
                    return sum(vals) / len(vals) if vals else None
                report = {
                    'network_called': False,
                    'sample_routes': sample_routes,
                    'constructed_provider_latency_seconds': latency,
                    'configured_qps': qps,
                    'max_concurrency': concurrency,
                    'wall_seconds': round(elapsed, 3),
                    'wall_throughput_qps': round(sample_routes / elapsed, 4) if elapsed else None,
                    'success': done['success'], 'failed': done['failed'],
                    'duplicate_observations': db.one('SELECT count(*)-count(DISTINCT observation_id) n FROM results WHERE job_id=?', (jid,))['n'],
                    'attempt_count': len(attempts),
                    'average_rate_wait_seconds': avg('rate_wait_seconds'),
                    'average_provider_seconds': avg('provider_duration_seconds'),
                    'average_raw_persistence_seconds': avg('raw_persistence_duration_seconds'),
                    'average_segment_persistence_seconds': avg('segment_persistence_duration_seconds'),
                    'average_database_seconds': avg('database_duration_seconds'),
                    'average_persistence_seconds': avg('persistence_duration_seconds'),
                    'average_total_seconds': avg('total_duration_seconds'),
                    'routes_2914_pure_limit_seconds': 2914 / qps,
                    'routes_2914_pure_limit_minutes': 2914 / qps / 60,
                    'routes_2914_required_throughput_qps': 2914 / (30 * 60),
                    'routes_2914_offline_projection_minutes': (2914 / (sample_routes / elapsed) / 60) if elapsed else None,
                    'performance_evidence': '构造延迟的隔离离线墙钟基准，不是高德真实吞吐验证',
                }
                return report
            finally:
                AmapProvider.route = original_route


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--sample-routes', type=int, default=60)
    parser.add_argument('--latency', type=float, default=0.75)
    parser.add_argument('--qps', type=float, default=3.0)
    parser.add_argument('--concurrency', type=int, default=3)
    args = parser.parse_args()
    result = run(args.sample_routes, args.latency, args.qps, args.concurrency)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    output = Path(__file__).resolve().parents[1] / '性能吞吐离线基准结果.json'
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'写入隔离基准摘要：{output}')
