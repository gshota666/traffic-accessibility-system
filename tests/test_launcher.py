import json
import os
import subprocess
import sys
import time
from pathlib import Path
import httpx
from start import reserve_socket,launch
from utils.paths import data_dir

ROOT=Path(__file__).resolve().parents[1]
def ready(root,proc):
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        assert proc.poll() is None,'server exited unexpectedly'
        try:
            info=json.loads((root/'instance.json').read_text(encoding='utf-8'))
            url=f'http://127.0.0.1:{info["port"]}'
            with httpx.Client(trust_env=False,timeout=.5) as c:
                r=c.get(url+'/api/dashboard',headers={'x-session-token':info['token']})
            if r.status_code==200:return url,info
        except (OSError,ValueError,httpx.HTTPError):pass
        time.sleep(.1)
    raise AssertionError('server not ready')

def test_real_launcher_reopen_exit_and_port_conflict(tmp_path,monkeypatch):
    root=tmp_path/'中文 应用数据'
    blocker=reserve_socket(8000); port=blocker.getsockname()[1]
    proc=subprocess.Popen([sys.executable,str(ROOT/'start.py'),'--no-browser','--data-dir',str(root),'--port',str(port)],cwd=ROOT)
    try:
        url,info=ready(root,proc);assert info['port']!=port
        opened=[];monkeypatch.setattr('webbrowser.open',lambda url:opened.append(url))
        launch(root,no_browser=False)
        assert len(opened)==1 and f':{info["port"]}' in opened[0]
        with httpx.Client(base_url=url,headers={'x-session-token':info['token']},trust_env=False) as c:
            assert c.post('/api/targets',json={'name':'持久化中文测试','lon':114.3,'lat':30.5}).status_code==200
            assert c.post('/api/shutdown').status_code==200
        proc.wait(timeout=30);assert proc.returncode==0
        assert not (root/'instance.json').exists()
        proc=subprocess.Popen([sys.executable,str(ROOT/'start.py'),'--no-browser','--data-dir',str(root)],cwd=ROOT)
        url,info=ready(root,proc)
        with httpx.Client(base_url=url,headers={'x-session-token':info['token']},trust_env=False) as c:
            assert any(t['name']=='持久化中文测试' for t in c.get('/api/targets').json())
            c.post('/api/shutdown')
        proc.wait(timeout=30);assert proc.returncode==0
    finally:
        blocker.close()
        if proc.poll() is None:proc.kill();proc.wait()

def test_paths(tmp_path,monkeypatch):
    monkeypatch.setenv('TRAFFIC_DATA_DIR',str(tmp_path/'路径 with spaces'))
    p=data_dir(); assert p.is_dir() and (p/'logs').is_dir()
    monkeypatch.delenv('TRAFFIC_DATA_DIR')
    from platformdirs import user_data_path
    assert data_dir()==user_data_path('TrafficAccessibility',appauthor=False)

def test_crash_recovery_native_process(tmp_path):
    root=tmp_path/'崩溃恢复'
    args=[sys.executable,str(ROOT/'start.py'),'--no-browser','--data-dir',str(root)]
    proc=subprocess.Popen(args,cwd=ROOT)
    try:
        url,info=ready(root,proc)
        with httpx.Client(base_url=url,headers={'x-session-token':info['token']},trust_env=False,timeout=30) as c:
            data='ID,名称,lon,lat\n'+''.join(f'A{i},测试点,114.{i:03},30.5\n' for i in range(10))
            upload=c.post('/api/imports',files={'file':('崩溃测试.csv',data.encode())}).json()
            assert c.post(f'/api/imports/{upload["id"]}/validate',json={'mapping':upload['mapping'],'crs':'WGS84'}).status_code==200
            j=c.post('/api/jobs',json={'name':'崩溃恢复','dataset_id':upload['id'],'target_ids':list(range(1,11)),'date':'2026-09-15','times':['08:00']}).json()
            for _ in range(300):
                before=c.get('/api/jobs/'+j['id']).json()
                if before['success']>0:break
                time.sleep(.02)
            assert 0<before['success']<100
        proc.kill();proc.wait(timeout=10)
        proc=subprocess.Popen(args,cwd=ROOT)
        url,info=ready(root,proc)
        with httpx.Client(base_url=url,headers={'x-session-token':info['token']},trust_env=False,timeout=30) as c:
            resumed=c.get('/api/jobs/'+j['id']).json()
            assert resumed['status']=='paused' and resumed['success']>=before['success']
            saved=c.get(f'/api/jobs/{j["id"]}/results?status=success&size=1').json()['rows'][0]
            c.post(f'/api/jobs/{j["id"]}/resume')
            recovery_deadline=time.monotonic()+360
            while time.monotonic()<recovery_deadline:
                final=c.get('/api/jobs/'+j['id']).json()
                if final['status']=='completed':break
                time.sleep(.05)
            assert final['success']==100
            import sqlite3
            with sqlite3.connect(root/'traffic.db') as db:
                db.row_factory=sqlite3.Row
                after=dict(db.execute('SELECT * FROM results WHERE id=?',(saved['id'],)).fetchone())
            assert after['id']==saved['id'] and after['calculated_at']==saved['calculated_at']
            c.post('/api/shutdown')
        proc.wait(timeout=30)
        import sqlite3
        with sqlite3.connect(root/'traffic.db') as conn: assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    finally:
        if proc.poll() is None:proc.kill();proc.wait()

def test_bulk_ui_controls_are_present():
    page=(ROOT/'frontend'/'app.js').read_text(encoding='utf-8')
    for text in ('转换全部目标','删除选中任务','删除选中数据','选择全部可删除任务','选择全部可删除数据'):
        assert text in page

def test_frontend_version_matches_backend_version():
    """The browser must not block startup because of a stale hard-coded version."""
    import re
    from utils.version import APP_VERSION
    page=(ROOT/'frontend'/'app.js').read_text(encoding='utf-8')
    match=re.search(r"version\.version!==['\"]([^'\"]+)",page)
    assert match and match.group(1)==APP_VERSION

def test_collection_time_reference_is_informational():
    js=(ROOT/'frontend/app.js').read_text(encoding='utf-8')
    section=js[js.index('function collectionSettings'):js.index('const localTime=')]
    for text in ['参考情景时间','03:00 或静态路网时间','14:00','08:00','18:00','周六/周日 10:00','节假日','单独分析']:
        assert text in section
    assert "wizard.collection_mode||'immediate'" in section
    assert 'id="scenario"' not in section
