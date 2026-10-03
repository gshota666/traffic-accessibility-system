import io
import sqlite3
import zipfile
from pathlib import Path
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from backend.app import create_app
from backend.db import Database


def make_app(tmp_path):
    return create_app(tmp_path / 'research-data', token='test-token', run_worker=False)


def import_sources(client):
    body='source_id,source_name,longitude,latitude,province,city,population,population_year,population_scope,population_source\nS1,来源甲,114.30,30.50,湖北,武汉,1000000,2020,城区,统计年鉴\nS2,来源乙,112.95,28.20,湖南,长沙,,2020,城区,统计年鉴\nS3,来源丙,115.90,28.70,江西,南昌,800000,2020,城区,统计年鉴\n'
    uploaded=client.post('/api/imports',files={'file':('sources.csv',body.encode())}).json()
    checked=client.post(f"/api/imports/{uploaded['id']}/validate",json={'mapping':uploaded['mapping'],'crs':'WGS84'})
    assert checked.status_code==200,checked.text
    return uploaded['id']


def test_row_coordinate_system_and_duplicate_source_id_are_explicit(tmp_path):
    app=make_app(tmp_path)
    with TestClient(app,headers={'x-session-token':'test-token'}) as client:
        body='source_id,source_name,longitude,latitude,coordinate_system\nS1,甲,114.30,30.50,GCJ-02\nS1,乙,114.31,30.51,WGS84\n'
        uploaded=client.post('/api/imports',files={'file':('mixed.csv',body.encode())}).json()
        checked=client.post(f"/api/imports/{uploaded['id']}/validate",json={'mapping':uploaded['mapping'],'crs':'WGS84'})
        assert checked.status_code==200 and checked.json()['duplicate']==1
        rows=client.get(f"/api/datasets/{uploaded['id']}/points?size=10").json()['rows']
        assert [r['crs'] for r in rows]==['GCJ-02','WGS84']
        assert rows[0]['source_id']=='S1' and rows[1]['source_id']=='S1-row-3'

        collision=client.post('/api/imports',files={'file':('collision.csv',b'source_id,source_name,longitude,latitude\nS1,one,114.3,30.5\nS1,two,114.31,30.51\nS1-row-3,three,114.32,30.52\n')}).json()
        checked=client.post(f"/api/imports/{collision['id']}/validate",json={'mapping':collision['mapping'],'crs':'WGS84'})
        assert checked.status_code==200
        rows=client.get(f"/api/datasets/{collision['id']}/points?size=10").json()['rows']
        assert len({r['source_id'] for r in rows})==3


def test_bidirectional_snapshot_attempts_and_package(tmp_path):
    app=make_app(tmp_path)
    with TestClient(app,headers={'x-session-token':'test-token'}) as client:
        dataset=import_sources(client)
        created=client.post('/api/jobs',json={
            'name':'研究双向试采','dataset_id':dataset,'target_ids':[1,2,3,4],
            'provider':'mock','directions':['outbound','inbound'],
            'mode':'exact','collection_mode':'immediate',
        })
        assert created.status_code==200,created.text
        job=created.json()
        app.state.db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
        app.state.worker.run(app.state.db.one('SELECT * FROM jobs WHERE id=?',(job['id'],)))
        done=client.get('/api/jobs/'+job['id']).json()
        assert done['total']==24 and done['success']==24 and done['status']=='completed'
        counts=app.state.db.rows('SELECT direction,count(*) n FROM results WHERE job_id=? GROUP BY direction',(job['id'],))
        assert {r['direction']:r['n'] for r in counts}=={'outbound':12,'inbound':12}
        assert app.state.db.one('SELECT count(*) n FROM request_attempts WHERE job_id=?',(job['id'],))['n']==24
        row=app.state.db.one('SELECT raw_response_path FROM results WHERE job_id=? LIMIT 1',(job['id'],))
        assert row['raw_response_path'] and (app.state.db.path.parent/'raw_responses'/row['raw_response_path']).is_file()
        ids=app.state.db.one('SELECT source_id,destination_id,origin_id,route_target_id FROM results WHERE job_id=? AND direction=\'inbound\' LIMIT 1',(job['id'],))
        assert ids['source_id']=='S1' and ids['destination_id'] in {'1','2','3','4'} and ids['origin_id']==ids['destination_id'] and ids['route_target_id']=='S1'
        detail=client.get(f"/api/jobs/{job['id']}/results?od_direction=inbound").json()
        assert detail['total']==12 and all(r['direction']=='inbound' for r in detail['rows'])
        package=client.get(f"/api/jobs/{job['id']}/export?kind=package")
        assert package.status_code==200 and package.headers['content-type'].startswith('application/zip')
        assert b'observations.csv' in package.content
        with zipfile.ZipFile(io.BytesIO(package.content)) as z:
            names=set(z.namelist())
            assert {'quality_report.csv','raw_response_index.csv'} <= names
            assert any(name.startswith('raw_responses/') for name in names)
        package_no_raw=client.get(f"/api/jobs/{job['id']}/export?kind=package&include_raw=false")
        assert package_no_raw.status_code==200
        with zipfile.ZipFile(io.BytesIO(package_no_raw.content)) as z:
            assert 'quality_report.csv' in z.namelist()
            assert not any(name.startswith('raw_responses/') for name in z.namelist())



def test_schedule_expands_per_date_without_past_or_cross_date_mix(tmp_path):
    app=make_app(tmp_path)
    with TestClient(app,headers={'x-session-token':'test-token'}) as client:
        dataset=import_sources(client)
        payload={'name':'研究日历','dataset_id':dataset,'target_ids':[1,2,3,4],
                 'provider':'mock','directions':['outbound','inbound'],'collection_mode':'scheduled',
                 'schedule':[
                     {'date':'2099-10-01','day_type':'holiday','times':['08:00','14:00'],'window_minutes':30,'notes':'短假'},
                     {'date':'2099-10-02','day_type':'holiday','times':['10:00','18:00'],'window_minutes':45,'notes':'第二天'},
                 ]}
        response=client.post('/api/jobs',json=payload)
        assert response.status_code==200,response.text
        job=response.json()
        assert job['total']==96
        assert len(job['config']['departures'])==4
        assert job['config']['directions']==['outbound','inbound']
        assert {x['day_type'] for x in job['config']['departure_metadata'].values()}=={'holiday'}
        assert all(x.get('population') is not None or x.get('source_id')=='S2' for x in job['config']['source_snapshot'])


def test_same_date_can_have_multiple_non_overlapping_time_points(tmp_path):
    """A real-data scheduler trial may sample several times on one date."""
    app=make_app(tmp_path)
    with TestClient(app,headers={'x-session-token':'test-token'}) as client:
        dataset=import_sources(client)
        base={'name':'同日多时点试采','dataset_id':dataset,'target_ids':[1,2],
              'provider':'mock','directions':['outbound'],'collection_mode':'scheduled',
              'mode':'exact','acknowledge_capacity':True}
        payload=dict(base,schedule=[
            {'date':'2099-10-01','day_type':'holiday','times':['08:00'],'window_minutes':30,'directions':['outbound']},
            {'date':'2099-10-01','day_type':'holiday','times':['10:00'],'window_minutes':30,'directions':['outbound']},
        ])
        response=client.post('/api/jobs',json=payload)
        assert response.status_code==200,response.text
        job=response.json()
        assert len(job['config']['departures'])==2
        assert job['total']==3*2*2
        overlap=dict(base,schedule=[
            {'date':'2099-10-01','day_type':'holiday','times':['08:00'],'window_minutes':30,'directions':['outbound']},
            {'date':'2099-10-01','day_type':'holiday','times':['08:20'],'window_minutes':30,'directions':['outbound']},
        ])
        rejected=client.post('/api/jobs',json=overlap)
        assert rejected.status_code==400 and '窗口不能重叠' in rejected.text


def test_schedule_can_override_direction_per_date(tmp_path):
    app=make_app(tmp_path)
    with TestClient(app,headers={'x-session-token':'test-token'}) as client:
        dataset=import_sources(client)
        response=client.post('/api/jobs',json={
            'name':'按日期方向','dataset_id':dataset,'target_ids':[1,2],
            'provider':'mock','directions':['outbound','inbound'],'collection_mode':'scheduled',
            'schedule':[
                {'date':'2099-10-01','day_type':'holiday','times':['08:00'],
                 'directions':['outbound'],'window_minutes':30},
                {'date':'2099-10-02','day_type':'holiday','times':['08:00'],
                 'directions':['outbound','inbound'],'window_minutes':30},
            ]})
        assert response.status_code==200,response.text
        job=response.json()
        # 3 sources × 2 destinations × (1 + 2 directions)
        assert job['total']==18
        metadata=job['config']['departure_metadata']
        assert {tuple(v['directions']) for v in metadata.values()}=={('outbound',),('outbound','inbound')}
        app.state.db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
        app.state.worker.prepare(app.state.db.one('SELECT * FROM jobs WHERE id=?',(job['id'],)),job['config'])
        counts=app.state.db.rows('SELECT departure,direction,count(*) n FROM results WHERE job_id=? GROUP BY departure,direction',(job['id'],))
        assert sum(r['n'] for r in counts)==18
        report=client.get('/api/jobs/'+job['id']+'/research').json()
        assert sorted((w['expected'],w['bidirectional_pairs']) for w in report['windows'])==[(6,0),(12,0)]


def test_old_database_migration_is_backed_up_and_idempotent(tmp_path):
    root=tmp_path/'legacy-data'; root.mkdir()
    path=root/'traffic.db'
    with sqlite3.connect(path) as conn:
        conn.executescript('''
            CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE jobs(id TEXT PRIMARY KEY,name TEXT,created TEXT,status TEXT,dataset_id TEXT,config TEXT,total INTEGER,prepared INTEGER,success INTEGER,failed INTEGER,message TEXT);
            CREATE TABLE results(id INTEGER PRIMARY KEY,job_id TEXT,point_pk INTEGER,target_id INTEGER,departure TEXT,status TEXT,provider TEXT);
            INSERT INTO settings VALUES('seeded','true');
            INSERT INTO jobs(id,name,created,status,total) VALUES('legacy','旧任务','2025-01-01','completed',1);
            INSERT INTO results(id,job_id,point_pk,target_id,departure,status,provider)
                VALUES(1,'legacy',2,3,'2025-01-01T08:00:00+08:00','success','mock');
            PRAGMA user_version=6;
        ''')
    Database(root)
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0]==12
        row=conn.execute('SELECT status,provider,direction,source_id,destination_id,window_miss_reason FROM results WHERE id=1').fetchone()
        assert row==('success','mock','outbound','','','')
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    backups=list(root.glob('traffic.pre-migration-*.db'))
    assert len(backups)==1
    Database(root)
    assert len(list(root.glob('traffic.pre-migration-*.db')))==1


def test_formal_research_csv_aliases_prefer_gcj02_and_keep_quality_metadata(tmp_path):
    app=make_app(tmp_path)
    source='origin_id,origin_name,market_unit,urban_population,wgs84_lon,wgs84_lat,gcj02_lon,gcj02_lat,routing_coord_system,quality_flag,coordinate_method,ready_for_routing\nO01,武汉市,武汉市,1000,114.304660,30.589006,114.310102,30.586593,GCJ-02,OK,转换记录,TRUE\n'
    with TestClient(app,headers={'x-session-token':'test-token'}) as client:
        uploaded=client.post('/api/imports',files={'file':('31客源单元_动态路线简化输入表.csv',source.encode())}).json()
        assert uploaded['mapping']['point_id']==0
        assert uploaded['mapping']['lon']==6 and uploaded['mapping']['lat']==7
        assert uploaded['crs']=='GCJ-02'
        checked=client.post(f"/api/imports/{uploaded['id']}/validate",json={'mapping':uploaded['mapping'],'crs':'WGS84'})
        assert checked.status_code==200 and checked.json()['valid']==1
        row=client.get(f"/api/datasets/{uploaded['id']}/points?size=1").json()['rows'][0]
        assert row['point_id']=='O01' and row['crs']=='GCJ-02'
        assert row['lon']==114.310102 and row['lat']==30.586593
        assert row['population']==1000 and '坐标方法' in row['notes']


def test_legacy_13_day_plan_capacity_remains_compatible(tmp_path):
    app=make_app(tmp_path)
    with TestClient(app,headers={'x-session-token':'test-token'}) as client:
        dataset=import_sources(client)
        plan=[]
        for day,typ,times in [
            *[(f'2099-10-{i:02d}','workday',['07:00','09:00','12:00','17:00','20:00']) for i in range(1,5)],
            *[(f'2099-10-{i:02d}','weekend',['07:00','09:00','12:00','17:00','20:00']) for i in range(5,9)],
            ('2099-10-09','holiday',['17:00','20:00','22:00']),
            ('2099-10-10','holiday',['06:00','08:00','10:00','12:00','14:00','17:00','20:00']),
            ('2099-10-11','holiday',['07:00','09:00','12:00','17:00','20:00']),
            ('2099-10-12','holiday',['10:00','12:00','14:00','16:00','18:00','20:00']),
            ('2099-10-13','holiday',['10:00','12:00','14:00','16:00','18:00','20:00']),
        ]:
            plan.append({'date':day,'day_type':typ,'times':times,'window_minutes':60,'directions':['outbound','inbound']})
        assert len(plan)==13 and sum(len(x['times']) for x in plan)==67
        estimate=client.post('/api/jobs/estimate',json={'name':'13天方案规模','dataset_id':dataset,'target_ids':[1,2,3,4], 'provider':'mock','directions':['outbound','inbound'],'collection_mode':'scheduled','schedule':plan,'mode':'exact','estimate_seconds':2}).json()
        # This fixture has 3 valid sources and 4 targets; the same 67-round
        # expansion is used for the full 31×94 study.
        assert estimate['batches']==67 and estimate['total_routes']==3*4*2*67


def test_word_12_day_plan_capacity_has_correct_direction_slices(tmp_path):
    app=make_app(tmp_path)
    with TestClient(app,headers={'x-session-token':'test-token'}) as client:
        dataset=import_sources(client)
        both=['outbound','inbound']
        plan=[
            {'date':'2099-09-30','phase':'国庆前夕','day_type':'holiday','times':['17:00','20:00','22:00'],'window_minutes':60,'directions':['outbound']},
            {'date':'2099-10-01','phase':'国庆首日','day_type':'holiday','times':['06:00','08:00','10:00','12:00','14:00','17:00','20:00'],'window_minutes':60,'directions':['outbound']},
            {'date':'2099-10-04','phase':'国庆中段','day_type':'holiday','times':['07:00','09:00','12:00','17:00','20:00'],'window_minutes':60,'directions':both},
            {'date':'2099-10-06','phase':'国庆返程阶段','day_type':'holiday','times':['10:00','12:00','14:00','16:00','18:00','20:00'],'window_minutes':60,'directions':['inbound']},
            {'date':'2099-10-07','phase':'国庆返程阶段','day_type':'holiday','times':['10:00','12:00','14:00','16:00','18:00','20:00'],'window_minutes':60,'directions':['inbound']},
            *[{'date':day,'phase':'普通工作日','day_type':'workday','times':['07:00','09:00','12:00','17:00','20:00'],'window_minutes':60,'directions':both} for day in ('2099-10-13','2099-10-14','2099-10-15')],
            {'date':'2099-10-17','phase':'普通周末Ⅰ·周六','day_type':'weekend','times':['07:00','09:00','12:00','17:00','20:00'],'window_minutes':60,'directions':both},
            {'date':'2099-10-18','phase':'普通周末Ⅰ·周日','day_type':'weekend','times':['07:00','09:00','12:00','17:00','20:00'],'window_minutes':60,'directions':both},
            {'date':'2099-10-24','phase':'普通周末Ⅱ·周六','day_type':'weekend','times':['07:00','09:00','12:00','17:00','20:00'],'window_minutes':60,'directions':both},
            {'date':'2099-10-25','phase':'普通周末Ⅱ·周日','day_type':'weekend','times':['07:00','09:00','12:00','17:00','20:00'],'window_minutes':60,'directions':both},
        ]
        assert len(plan)==12 and sum(len(x['times']) for x in plan)==62
        assert sum(len(x['times'])*len(x['directions']) for x in plan)==102
        estimate=client.post('/api/jobs/estimate',json={'name':'12天方案规模','dataset_id':dataset,'target_ids':[1,2,3,4], 'provider':'mock','directions':both,'collection_mode':'scheduled','schedule':plan,'mode':'exact','estimate_seconds':2,'schedule_template':'formal-2026-12day-v1'}).json()
        assert estimate['batches']==62 and estimate['natural_time_slots']==62
        assert estimate['direction_time_slices']==102
        assert estimate['total_routes']==3*4*102
        created=client.post('/api/jobs',json={'name':'12天计划快照','dataset_id':dataset,'target_ids':[1,2,3,4], 'provider':'mock','directions':both,'collection_mode':'scheduled','schedule':plan,'mode':'exact','estimate_seconds':2,'schedule_template':'formal-2026-12day-v1'})
        assert created.status_code==200,created.text
        job=created.json()
        assert job['total']==3*4*102
        assert job['config']['schedule_template']=='formal-2026-12day-v1'
        assert job['config']['departure_metadata']['2099-09-30T17:00:00+08:00']['phase']=='国庆前夕'
        assert job['config']['departure_metadata']['2099-10-01T06:00:00+08:00']['directions']==['outbound']


def test_trial_can_select_source_rows_without_copying_csv(tmp_path):
    app=make_app(tmp_path)
    with TestClient(app,headers={'x-session-token':'test-token'}) as client:
        dataset=import_sources(client)
        rows=client.get(f'/api/source-nodes?dataset_id={dataset}').json()
        selected=[rows[0]['id'],rows[1]['id']]
        estimate=client.post('/api/jobs/estimate',json={
            'name':'两点试采规模','dataset_id':dataset,'source_point_ids':selected,
            'target_ids':[1,2],'provider':'mock','directions':['outbound','inbound'],
            'collection_mode':'immediate','mode':'exact'}).json()
        assert estimate['points']==2 and estimate['total_routes']==8
        created=client.post('/api/jobs',json={
            'name':'两点试采','dataset_id':dataset,'source_point_ids':selected,
            'target_ids':[1,2],'provider':'mock','directions':['outbound','inbound'],
            'collection_mode':'immediate','mode':'exact'} )
        assert created.status_code==200,created.text
        job=created.json()
        assert job['total']==8 and len(job['config']['source_snapshot'])==2
