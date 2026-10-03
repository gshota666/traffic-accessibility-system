"""Bounded, offline preparation benchmark. Does not issue any network requests."""
import csv
import io
import json
import tempfile
import time
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from backend.app import create_app
if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='traffic-scale-') as tmp:
        app=create_app(tmp,token='benchmark',run_worker=False)
        with TestClient(app,headers={'x-session-token':'benchmark'}) as c:
            buf=io.StringIO();w=csv.writer(buf);w.writerow(['ID','名称','lon','lat'])
            for i in range(10000):w.writerow([f'P{i:05}','规模测试',110+i%100*.01,30+i//100*.01])
            start=time.perf_counter()
            upload=c.post('/api/imports',files={'file':('规模.csv',buf.getvalue().encode())}).json()
            result=c.post(f'/api/imports/{upload["id"]}/validate',json={'mapping':upload['mapping'],'crs':'WGS84'}).json()
            imported=time.perf_counter()
            j=c.post('/api/jobs',json={'name':'31万组合准备测试','dataset_id':upload['id'],'target_ids':list(range(1,32)),'date':'2026-09-15','times':['08:00']}).json()
            db=app.state.db;db.execute("UPDATE jobs SET status='running' WHERE id=?",(j['id'],))
            app.state.worker.prepare(j,j['config'])
            prepared=time.perf_counter()
            response=c.get(f'/api/jobs/{j["id"]}/results?size=50&page=3000&sort=duration_seconds')
            paged=time.perf_counter()
            assert response.json()['total']==310000 and len(response.json()['rows'])==50
            report={'points':result['valid'],'prepared_combinations':310000,'import_seconds':round(imported-start,3),'preparation_seconds':round(prepared-imported,3),'page_seconds':round(paged-prepared,3),'page_bytes':len(response.content),'actual_route_calls':0,'note':'准备、持久化及分页测试；并非31万条真实地图调用测试'}
            print(json.dumps(report,ensure_ascii=False,indent=2))
            (Path(__file__).resolve().parents[1]/'性能基准结果.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
