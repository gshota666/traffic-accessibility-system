"""Offline throughput and bounded-concurrency checks.

These tests replace the provider with a deterministic in-process function; no
HTTP request or API key is used.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from utils.paths import resource

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr('services.settings.keyring.get_password',lambda *args: None)
    app=create_app(tmp_path / 'throughput-data',token='test-token',run_worker=False)
    with TestClient(app,headers={'x-session-token':'test-token'}) as c:
        yield c

def imported(c,content=None):
    data=content if content is not None else (resource('resources')/'test_points.csv').read_bytes()
    response=c.post('/api/imports',files={'file':('offline.csv',data)})
    assert response.status_code==200,response.text
    uploaded=response.json()
    response=c.post(f'/api/imports/{uploaded["id"]}/validate',json={'mapping':uploaded['mapping'],'crs':'GCJ-02'})
    assert response.status_code==200,response.text
    return uploaded['id'],response.json()

def job(c,dataset,**extra):
    body=dict(name='离线吞吐',dataset_id=dataset,target_ids=[1],provider='mock',times=['08:00'])
    body.update(extra)
    response=c.post('/api/jobs',json=body)
    assert response.status_code==200,response.text
    return response.json()

def run(c,j):
    c.app.state.db.execute("UPDATE jobs SET status='running' WHERE id=?",(j['id'],))
    c.app.state.worker.run(c.app.state.db.one('SELECT * FROM jobs WHERE id=?',(j['id'],)))
    return c.get('/api/jobs/'+j['id']).json()

from providers.amap import AmapProvider
from providers.base import RouteResult
from services.worker import SmoothRateLimiter
from services.settings import Settings


def test_smooth_rate_limiter_has_no_burst():
    limiter=SmoothRateLimiter(3)
    stop=threading.Event()
    starts=[]
    for _ in range(5):
        allowed,_=limiter.acquire(stop)
        assert allowed
        starts.append(time.monotonic())
    intervals=[b-a for a,b in zip(starts,starts[1:])]
    assert min(intervals)>=0.30
    # A smooth schedule also respects the provider-facing sliding-window
    # interpretation: no one-second window contains a fourth start.
    for i,start in enumerate(starts):
        assert sum(1 for other in starts if start <= other < start+1.0-1e-6) <= 3
    def acquire_and_record():
        allowed,_=limiter.acquire(stop)
        return allowed,time.monotonic()
    with ThreadPoolExecutor(max_workers=5) as pool:
        concurrent_results=[future.result() for future in [pool.submit(acquire_and_record) for _ in range(5)]]
    assert all(allowed for allowed,_ in concurrent_results)
    concurrent_starts=sorted(start for _,start in concurrent_results)
    assert all(sum(1 for other in concurrent_starts if start <= other < start+1.0-1e-6)<=3 for start in concurrent_starts)


def test_amap_worker_uses_bounded_concurrency_without_network(client,monkeypatch):
    monkeypatch.setattr(Settings,'key',lambda _: 'offline-test-key')
    client.app.state.worker.settings.save(dict(provider='amap',qps=20,max_concurrency=3,daily_limit=100,retries=0,strategy='32'))
    active=0
    maximum=0
    lock=threading.Lock()

    def fake_route(self,origin,dest,*args):
        nonlocal active,maximum
        with lock:
            active+=1
            maximum=max(maximum,active)
        time.sleep(0.03)
        with lock:
            active-=1
        return RouteResult(1234,321,'current_estimate','amap','10000')

    monkeypatch.setattr(AmapProvider,'route',fake_route)
    dataset,_=imported(client,content=b'ID,Name,lon,lat\na,Alpha,114.3,30.5\nb,Beta,114.4,30.6\nc,Gamma,114.5,30.7\n')
    created=job(client,dataset,target_ids=[1],provider='amap',acknowledge_estimate=True,acknowledge_capacity=True,times=['08:00'])
    done=run(client,created)
    assert done['status']=='completed' and done['success']==3
    assert maximum<=3
    attempts=client.app.state.db.rows('SELECT max_concurrency,provider_duration_seconds,total_duration_seconds FROM request_attempts WHERE job_id=?',(created['id'],))
    assert len(attempts)==3 and {row['max_concurrency'] for row in attempts}=={3}
    assert all(row['provider_duration_seconds'] is not None for row in attempts)
    assert client.app.state.db.one('SELECT count(*) n FROM results WHERE job_id=? AND inflight=1',(created['id'],))['n']==0


def test_offline_2914_observation_fixture_has_no_duplicates(client):
    source_lines=['source_id,source_name,longitude,latitude,population']
    for i in range(1,32):
        source_lines.append(f'O{i:02d},来源{i},114.{i:03d},30.{i:03d},{1000+i}')
    dataset,_=imported(client,('\n'.join(source_lines)+'\n').encode())
    # Keep the test database isolated and add a deterministic 94-destination
    # fixture; no provider is called in this preparation-only check.
    db=client.app.state.db
    target_ids=[]
    with db.connect() as conn:
        for i in range(1,95):
            cur=conn.execute('INSERT INTO targets(target_id,name,lon,lat,crs,note) VALUES(?,?,?,?,?,?)',(f'D{i:03d}',f'村{i}',114+i/1000,30+i/1000,'WGS84','offline fixture'))
            target_ids.append(cur.lastrowid)
    created=job(client,dataset,target_ids=target_ids,provider='mock',times=['10:00'])
    db.execute("UPDATE jobs SET status='running' WHERE id=?",(created['id'],))
    db_row=db.one('SELECT * FROM jobs WHERE id=?',(created['id'],))
    client.app.state.worker.prepare(db_row,created['config'])
    count=db.one('SELECT count(*) n FROM results WHERE job_id=?',(created['id'],))['n']
    distinct=db.one('SELECT count(DISTINCT observation_id) n FROM results WHERE job_id=?',(created['id'],))['n']
    assert count==31*94==2914 and distinct==2914
    assert db.one('SELECT count(*) n FROM results WHERE job_id=? AND direction!=\'outbound\'',(created['id'],))['n']==0
    assert 2914/3/60 < 30 and 2914/(30*60) == pytest.approx(1.6188888889)


def test_offline_2914_batch_executes_without_duplicates(client,monkeypatch):
    """Run the complete 2,914-row pipeline against an in-process provider.

    This is a bounded offline smoke benchmark, not an AMap performance claim:
    the fake provider has no network latency and uses a high local QPS so the
    test remains short while exercising claims, raw-file persistence, IDs and
    concurrent database writes.
    """
    monkeypatch.setattr(Settings,'key',lambda _: 'offline-test-key')
    client.app.state.worker.settings.save(dict(provider='amap',qps=1000,max_concurrency=3,daily_limit=4000,retries=0,strategy='32'))
    def fake_route(self,origin,dest,*args):
        return RouteResult(1234,321,'current_estimate','amap','10000',raw_response={'status':'1','offline':True})
    monkeypatch.setattr(AmapProvider,'route',fake_route)
    source_lines=['source_id,source_name,longitude,latitude,population']
    for i in range(1,32): source_lines.append(f'O{i:02d},来源{i},114.{i:03d},30.{i:03d},{1000+i}')
    dataset,_=imported(client,('\n'.join(source_lines)+'\n').encode())
    db=client.app.state.db; target_ids=[]
    with db.connect() as conn:
        for i in range(1,95):
            cur=conn.execute('INSERT INTO targets(target_id,name,lon,lat,crs,note) VALUES(?,?,?,?,?,?)',(f'D{i:03d}',f'村{i}',114+i/1000,30+i/1000,'WGS84','offline fixture'))
            target_ids.append(cur.lastrowid)
    created=job(client,dataset,target_ids=target_ids,provider='amap',times=['10:00'],acknowledge_estimate=True,acknowledge_capacity=True)
    started=time.monotonic(); done=run(client,created); elapsed=time.monotonic()-started
    assert done['status']=='completed' and done['success']==2914 and done['failed']==0
    assert db.one('SELECT count(*) n FROM results WHERE job_id=?',(created['id'],))['n']==2914
    assert db.one('SELECT count(DISTINCT observation_id) n FROM results WHERE job_id=?',(created['id'],))['n']==2914
    assert db.one('SELECT count(*) n FROM results WHERE job_id=? AND inflight=1',(created['id'],))['n']==0
    assert db.one('SELECT count(*) n FROM request_attempts WHERE job_id=?',(created['id'],))['n']==2914
    assert db.one('SELECT count(*) n FROM request_attempts WHERE job_id=? AND raw_response_path IS NOT NULL AND raw_response_path<>\'\'',(created['id'],))['n']==2914
    assert db.one('SELECT count(*) n FROM results WHERE job_id=? AND raw_response_path IS NOT NULL AND raw_response_path<>\'\'',(created['id'],))['n']==2914
    assert elapsed < 180, f'isolated local smoke unexpectedly slow: {elapsed:.1f}s'


def test_concurrent_daily_budget_is_atomic(client,monkeypatch):
    monkeypatch.setattr(Settings,'key',lambda _: 'offline-test-key')
    client.app.state.worker.settings.save(dict(provider='amap',qps=100,max_concurrency=3,daily_limit=4,retries=0,strategy='32'))
    def fake_route(self,origin,dest,*args):
        time.sleep(0.01)
        return RouteResult(1234,321,'current_estimate','amap','10000',raw_response={'offline':True})
    monkeypatch.setattr(AmapProvider,'route',fake_route)
    dataset,_=imported(client,content=b'ID,Name,lon,lat\na,Alpha,114.3,30.5\nb,Beta,114.31,30.51\nc,Gamma,114.32,30.52\nd,Delta,114.33,30.53\ne,Epsilon,114.34,30.54\n')
    created=job(client,dataset,target_ids=[1],provider='amap',acknowledge_estimate=True,acknowledge_capacity=True,times=['08:00'])
    done=run(client,created)
    db=client.app.state.db
    used=db.one("SELECT calls FROM usage WHERE provider='amap'")['calls']
    attempts=db.one('SELECT count(*) n FROM request_attempts WHERE job_id=?',(created['id'],))['n']
    assert used<=4 and attempts<=4 and done['status']=='paused'


def test_constructed_long_tail_latency_is_recorded(client,monkeypatch):
    """Scaled 2/5/15-second long tails exercise metrics without a long test.

    The 0.01 scale is deliberate: this is a constructed distribution check,
    not a claim that the real provider has exactly these frequencies.
    """
    monkeypatch.setattr(Settings,'key',lambda _: 'offline-test-key')
    client.app.state.worker.settings.save(dict(provider='amap',qps=100,max_concurrency=3,daily_limit=10,retries=0,strategy='32'))
    delays=iter([0.02,0.05,0.15])
    def fake_route(self,origin,dest,*args):
        time.sleep(next(delays))
        return RouteResult(1234,321,'current_estimate','amap','10000',raw_response={'offline':True})
    monkeypatch.setattr(AmapProvider,'route',fake_route)
    dataset,_=imported(client,content=b'ID,Name,lon,lat\na,Alpha,114.3,30.5\nb,Beta,114.31,30.51\nc,Gamma,114.32,30.52\n')
    created=job(client,dataset,target_ids=[1],provider='amap',acknowledge_estimate=True,acknowledge_capacity=True,times=['08:00'])
    done=run(client,created)
    assert done['status']=='completed' and done['success']==3
    durations=[r['provider_duration_seconds'] for r in client.app.state.db.rows('SELECT provider_duration_seconds FROM request_attempts WHERE job_id=?',(created['id'],))]
    assert max(durations)-min(durations)>0.08
