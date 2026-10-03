"""Smoke-test frozen executable with temporary Chinese paths on its native OS."""
import json
import io
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]
if __name__=='__main__':
    exe=ROOT/'dist'/'交通可达性分析系统.exe' if sys.platform=='win32' else ROOT/'dist'/'交通可达性分析系统.app'/'Contents'/'MacOS'/'交通可达性分析系统'
    with tempfile.TemporaryDirectory(prefix='交通包测试 ') as tmp:
        root=Path(tmp)
        smoke_env=os.environ.copy()
        # The frozen smoke test is offline and must not wait on a desktop
        # keychain prompt; production runs still use the configured keyring.
        smoke_env['PYTHON_KEYRING_BACKEND']='keyring.backends.null.Keyring'
        proc=subprocess.Popen([str(exe),'--no-browser','--data-dir',str(root)],env=smoke_env)
        try:
            for _ in range(600):
                if proc.poll() is not None: raise RuntimeError(f'Frozen app exited: {proc.returncode}')
                try:
                    state=json.loads((root/'instance.json').read_text())
                    client=httpx.Client(base_url=f'http://127.0.0.1:{state["port"]}',headers={'x-session-token':state['token']},trust_env=False,timeout=2)
                    if client.get('/health').status_code==200: break
                    client.close()
                except (OSError,ValueError,httpx.HTTPError):pass
                time.sleep(.1)
            else: raise RuntimeError('Frozen app failed to start')
            client.close()
            with httpx.Client(base_url=f'http://127.0.0.1:{state["port"]}',headers={'x-session-token':state['token']},trust_env=False,timeout=30) as client:
                assert client.get('/').status_code==200
                assert len(client.get('/api/targets').json())==31
                sample=client.get('/api/sample/xlsx').content
                imported=client.post('/api/imports',files={'file':('中文点位.xlsx',sample)}).json()
                r=client.post(f'/api/imports/{imported["id"]}/validate',json={'mapping':imported['mapping'],'crs':'GCJ-02'})
                assert r.status_code==200,r.text
                j=client.post('/api/jobs',json={'name':'打包验收','dataset_id':imported['id'],'target_ids':[1,2,3],'date':'2026-09-15','times':['08:00']}).json()
                # The packaged worker honors the default QPS limiter; allow
                # enough time for 39 Mock routes even on a cold PyInstaller
                # startup instead of making the smoke test timing-sensitive.
                for _ in range(600):
                    result=client.get('/api/jobs/'+j['id']).json()
                    if result['status']=='completed':break
                    time.sleep(.1)
                assert result['success']==39,result
                export=client.get(f'/api/jobs/{j["id"]}/export')
                assert export.status_code==200 and export.content[:2]==b'PK'
                paper=client.get(f'/api/jobs/{j["id"]}/export?kind=paper')
                quality=client.get(f'/api/jobs/{j["id"]}/export?kind=quality')
                assert paper.status_code==200 and paper.content[:2]==b'PK'
                assert quality.status_code==200 and quality.content[:2]==b'PK'
                package=client.get(f'/api/jobs/{j["id"]}/export?kind=package&include_raw=false')
                assert package.status_code==200 and package.content[:2]==b'PK'
                with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
                    assert '路线观测明细.csv' in archive.namelist()
                report=client.get(f'/api/jobs/{j["id"]}/research')
                assert report.status_code==200 and report.json()['windows'][0]['eligible']==39,report.text
                exported_report=client.get(f'/api/jobs/{j["id"]}/export?kind=research')
                assert exported_report.status_code==200 and exported_report.content[:2]==b'PK'
                scheduled=client.post('/api/jobs',json={'name':'多日打包验收','dataset_id':imported['id'],'target_ids':[1],'dates':['2099-09-15','2099-09-16'],'times':['08:00','14:00'],'collection_mode':'scheduled','random_seed':42})
                assert scheduled.status_code==200,scheduled.text
                assert len(scheduled.json()['config']['departures'])==4
                assert client.get('/api/version').json()['version']=='2026.09.21.10'
                client.post('/api/shutdown')
            proc.wait(timeout=30)
            assert proc.returncode==0
            print('Frozen app: startup, Chinese Excel import, Mock task, research report, multi-day schedule, export and shutdown passed.')
        finally:
            if proc.poll() is None:proc.kill();proc.wait()
