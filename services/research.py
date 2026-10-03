"""Snapshot completeness and matched-OD descriptive comparisons; no synthetic data."""
import json
import statistics
from datetime import datetime,timezone,timedelta
from itertools import combinations
from openpyxl import Workbook
from services.exporter import sheet,safe_cell,_label_value,_beijing,DIRECTION_LABELS,PROVIDER_LABELS

DAY_LABELS={'workday':'工作日','weekend':'周末','holiday':'节假日','custom':'自定义日期组'}

def describe(values):
    return dict(mean=statistics.mean(values) if values else None,
                median=statistics.median(values) if values else None,
                sd=statistics.stdev(values) if len(values)>1 else None,
                minimum=min(values) if values else None,maximum=max(values) if values else None)

def research_report(db,jid,at=None):
    at=at or datetime.now(timezone.utc)
    with db.connect() as conn:
        conn.execute('BEGIN')  # All tables and aggregates describe a single read snapshot.
        job=dict(conn.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())
        cfg=json.loads(job['config']); departures=cfg['departures']
        scheduled=cfg.get('collection_mode')=='scheduled'
        # Count planned observations per departure from the snapshot rather
        # than dividing the task total.  This remains correct when a calendar
        # overrides directions for only some dates.
        configured_base=(cfg.get('point_count') or 0) * (min(cfg.get('candidates',len(cfg.get('targets',[]))),len(cfg.get('targets',[]))) if cfg.get('mode')=='saving' else len(cfg.get('targets',[])))
        if not configured_base and departures:
            configured_base=job['total']//len(departures)
        expected_by_departure={dep:configured_base*len((cfg.get('departure_metadata') or {}).get(dep,{}).get('directions') or cfg.get('directions') or ['outbound']) for dep in departures}
        conn.execute('CREATE TEMP TABLE windows(dep TEXT PRIMARY KEY,start REAL,end REAL)')
        window_rows=[]
        for dep in departures:
            start=datetime.fromisoformat(dep).timestamp()
            meta=(cfg.get('departure_metadata') or {}).get(dep,{})
            window_rows.append((dep,start,start+int(meta.get('window_minutes',cfg.get('window_minutes',20)))*60))
        conn.executemany('INSERT INTO windows VALUES(?,?,?)',window_rows)
        # Only successful, freshly queried, time-qualified rows enter comparisons.
        conn.execute('''CREATE TEMP TABLE eligible AS SELECT r.point_pk,r.target_id,r.direction,r.departure,r.duration_seconds/60.0 minutes
            FROM results r JOIN windows w ON r.departure=w.dep
            WHERE r.job_id=? AND r.status='success' AND r.duration_seconds>=0 AND coalesce(r.cache_hit,0)=0
            AND coalesce(r.last_requested_at,r.requested_at) IS NOT NULL
            AND (?=0 OR (unixepoch(coalesce(r.last_requested_at,r.requested_at))>=w.start
                     AND unixepoch(coalesce(r.last_requested_at,r.requested_at))<w.end))''',(jid,int(scheduled)))
        conn.execute('CREATE INDEX eligible_od ON eligible(point_pk,target_id,direction,departure)')
        conn.execute('CREATE INDEX eligible_dep ON eligible(departure)')
        sums={r['departure']:dict(r) for r in conn.execute('''SELECT departure,count(*) prepared,
            sum(status='success') success,sum(status='failed') failed,
            sum(status='failed' AND error LIKE '错过采集窗口%') missed_failed,
            sum(requested_at IS NOT NULL) requested,min(requested_at) started,
            max(CASE WHEN requested_at IS NOT NULL THEN coalesce(response_received_at,fetched_at,calculated_at) END) finished,
            sum(coalesce(attempts,0)) attempts,sum(coalesce(response_crossed_window,0)) response_crossed,
            sum(status='success' AND traffic_coverage IS NOT NULL) traffic_routes,
            avg(CASE WHEN status='success' THEN traffic_coverage END) traffic_coverage FROM results WHERE job_id=? GROUP BY departure''',(jid,))}
        miss_reasons={}
        for row in conn.execute('''SELECT departure,window_miss_reason,COUNT(*) n FROM results
                                   WHERE job_id=? AND window_miss_reason IS NOT NULL AND window_miss_reason!=''
                                   GROUP BY departure,window_miss_reason''',(jid,)):
            miss_reasons.setdefault(row['departure'],{})[row['window_miss_reason']]=row['n']
        eligible={r['departure']:dict(r) for r in conn.execute('SELECT departure,count(*) n,avg(minutes) mean FROM eligible GROUP BY departure')}
        configured_directions=set(cfg.get('directions') or ['outbound'])
        # A one-way task has no evidence about the reverse direction.  Report
        # zero bidirectional pairs rather than accidentally treating every
        # successful one-way observation as a matched pair.
        paired_success={}
        for dep in departures:
            dep_dirs=set((cfg.get('departure_metadata') or {}).get(dep,{}).get('directions') or configured_directions)
            if dep_dirs=={'outbound','inbound'}:
                row=conn.execute('''SELECT count(*) n FROM
                    (SELECT point_pk,target_id,count(DISTINCT direction) directions FROM eligible
                     WHERE departure=? GROUP BY point_pk,target_id HAVING directions=2)''',(dep,)).fetchone()
                paired_success[dep]=row['n'] if row else 0
        windows=[]
        direction_sums={}
        for row in conn.execute('''SELECT departure,direction,count(*) planned,
                sum(status='success') success,sum(status='failed') failed,
                sum(requested_at IS NOT NULL) requested,
                sum(coalesce(attempts,0)) attempts,
                sum(coalesce(response_crossed_window,0)) response_crossed
            FROM results WHERE job_id=? GROUP BY departure,direction''',(jid,)):
            direction_sums[(row['departure'],row['direction'])]=dict(row)
        direction_windows=[]
        for dep,start,end in window_rows:
            a=sums.get(dep,{})
            expected=expected_by_departure.get(dep,0)
            success=a.get('success',0); failed=a.get('failed',0); pending=max(0,expected-success-failed)
            expired=scheduled and at.timestamp()>=end
            closed=success+failed>=expected or expired
            e=eligible.get(dep,{})
            meta=(cfg.get('departure_metadata') or {}).get(dep,{})
            dep_dirs=set(meta.get('directions') or configured_directions)
            bidirectional_applicable=dep_dirs=={'outbound','inbound'}
            windows.append(dict(departure=dep,date=dep[:10],time=dep[11:16],phase=meta.get('phase',''),day_type=DAY_LABELS.get(meta.get('day_type',cfg.get('day_type')),'未分类'),
                expected=expected,success=success,failed=failed,pending=pending,
                missed=a.get('missed_failed',0)+(pending if expired else 0),
                request_failures=failed-a.get('missed_failed',0),
                eligible=e.get('n',0),excluded_success=success-e.get('n',0),
                success_pct=round(success/expected*100,2) if expected else None,
                completeness_pct=round(e.get('n',0)/expected*100,2) if expected else None,
                started=a.get('started'),finished=a.get('finished'),attempts=a.get('attempts',0),requested=a.get('requested',0),
                window_end=datetime.fromtimestamp(end,timezone(timedelta(hours=8))).isoformat() if scheduled else None,
                state='已结束' if closed else ('等待中' if scheduled and at.timestamp()<start else '采集中 / 待继续'),
                response_crossed=a.get('response_crossed',0),traffic_routes=a.get('traffic_routes',0),traffic_coverage=a.get('traffic_coverage'),bidirectional_pairs=paired_success.get(dep,0),bidirectional_applicable=bidirectional_applicable,
                window_miss_reasons=miss_reasons.get(dep,{}),closed=closed,mean_minutes=e.get('mean')))
            for direction in (meta.get('directions') or configured_directions):
                a=direction_sums.get((dep,direction),{})
                planned=configured_base
                success=a.get('success',0); failed=a.get('failed',0)
                valid=conn.execute('''SELECT count(*) n FROM eligible WHERE departure=? AND direction=?''',(dep,direction)).fetchone()['n']
                direction_windows.append(dict(departure=dep,date=dep[:10],time=dep[11:16],phase=meta.get('phase',''),day_type=DAY_LABELS.get(meta.get('day_type',cfg.get('day_type')),'未分类'),direction=direction,
                    expected=planned,success=success,failed=failed,pending=max(0,planned-success-failed),eligible=valid,
                    completeness_pct=round(valid/planned*100,2) if planned else None,requested=a.get('requested',0),attempts=a.get('attempts',0),response_crossed=a.get('response_crossed',0),closed=closed))
        # Intersect OD identities across ALL closed batches of the date group.
        # A wholly missed batch makes the balanced sample empty, rather than biasing the mean.
        closed=[w for w in windows if w['closed']] if scheduled else []
        stats=[]; paired=[]
        if closed:
            conn.execute('CREATE TEMP TABLE closed(dep TEXT PRIMARY KEY)')
            conn.executemany('INSERT INTO closed VALUES(?)',[(w['departure'],) for w in closed])
            conn.execute('''CREATE TEMP TABLE balanced AS SELECT e.point_pk,e.target_id,e.direction
                FROM eligible e JOIN closed c ON e.departure=c.dep GROUP BY e.point_pk,e.target_id,e.direction HAVING count(*)=?''',(len(closed),))
            conn.execute('CREATE UNIQUE INDEX balanced_od ON balanced(point_pk,target_id,direction)')
            common=conn.execute('SELECT count(*) FROM balanced').fetchone()[0]
            means={r['departure']:r['mean'] for r in conn.execute('''SELECT e.departure,avg(e.minutes) mean
                FROM eligible e JOIN balanced b USING(point_pk,target_id,direction) JOIN closed c ON c.dep=e.departure GROUP BY e.departure''')}
            slots=sorted({w['time'] for w in closed})
            for slot in slots:
                batches=[w for w in closed if w['time']==slot]
                values=[means[w['departure']] for w in batches if w['departure'] in means]
                stats.append(dict(time=slot,day_type=windows[0]['day_type'],closed_days=len(batches),valid_days=len(values),common_od=common,
                    **describe(values)))
            # Paired time-of-day differences use the same dates AND same OD panel.
            for first,second in combinations(slots,2):
                left={w['date']:w for w in closed if w['time']==first}
                right={w['date']:w for w in closed if w['time']==second}
                dates=sorted(set(left)&set(right))
                deltas=[means[right[d]['departure']]-means[left[d]['departure']] for d in dates
                        if left[d]['departure'] in means and right[d]['departure'] in means]
                paired.append(dict(first=first,second=second,paired_days=len(deltas),common_od=common,**describe(deltas)))
        return dict(job_id=jid,name=job['name'],generated_at=at.isoformat(),provider=cfg['provider'],
            collection_mode=cfg.get('collection_mode','legacy'),day_type=cfg.get('day_type','custom'),directions=cfg.get('directions',['outbound']),study_id=cfg.get('study_id',''),
            strategy=cfg['strategy'],scope=cfg['mode'],window_minutes=cfg.get('window_minutes'),
            query_order=cfg.get('query_order','original'),random_seed=cfg.get('random_seed'),
            order_algorithm=cfg.get('order_algorithm','original'),schedule_template=cfg.get('schedule_template',''),schedule_adjusted=bool(cfg.get('schedule_adjusted',False)),schedule_window_confirmed=bool(cfg.get('schedule_window_confirmed',False)),windows=windows,direction_windows=direction_windows,statistics=stats,paired=paired,
            method='完整率=时窗内成功且非缓存的路线数/计划路线数。待采包含尚未准备的路线；错过窗口包含已标失败和过期未处理记录，与失败/待采可能重叠。多日统计仅纳入已结束批次，先取所有这些批次共同有效的起终点对，再计算各日平均耗时及跨日均值、中位数和样本标准差（分钟）；不足两天不计算标准差。时段差为相同日期的后时段减前时段。零共同样本不产生估计，均为描述统计。')

def export_research(report,path):
    wb=Workbook(write_only=True)
    specs=[('采集完整性','windows',[('departure','计划批次'),('phase','研究阶段'),('day_type','日期类型'),('expected','应采'),('success','成功'),('failed','失败'),('pending','待采'),('missed','错过窗口'),('window_miss_reasons','错过窗口原因统计'),('request_failures','其他失败'),('eligible','有效成功'),('excluded_success','排除的成功'),('bidirectional_applicable','双向配对是否适用'),('bidirectional_pairs','双向均成功配对数'),('completeness_pct','完整率 %'),('started','实际开始 UTC'),('finished','实际结束 UTC'),('attempts','请求尝试数'),('response_crossed','响应跨窗口数'),('traffic_routes','有分段路况路线数'),('traffic_coverage','平均路况覆盖率 %'),('window_end','窗口结束'),('state','状态'),('mean_minutes','有效路线平均分钟')]),
        ('多日统计','statistics',[('time','时段'),('day_type','日期类型'),('closed_days','已结束日期数'),('valid_days','有效日期数'),('common_od','共同起终点对数'),('mean','日均耗时的均值 min'),('median','日均耗时的中位数 min'),('sd','日均耗时的样本标准差 min'),('minimum','最小日均 min'),('maximum','最大日均 min')]),
        ('时段配对比较','paired',[('first','前时段'),('second','后时段'),('paired_days','配对日期数'),('common_od','共同起终点对数'),('mean','平均差 min'),('median','差值中位数 min'),('sd','差值样本标准差 min')])]
    for title,key,cols in specs:
        ws=sheet(wb,title,[label for _,label in cols])
        for row in report[key]:
            values=[]
            for k,_ in cols:
                value=row.get(k)
                if k=='direction': value=DIRECTION_LABELS.get(value,value)
                elif k in {'departure','window_end'}: value=_beijing(value)
                elif k=='bidirectional_applicable': value='是' if value else '不适用'
                values.append(safe_cell(ws,value))
            ws.append(values)
    ws=sheet(wb,'方法与参数',['参数','内容'])
    method_labels={'job_id':'任务编号','name':'任务名称','generated_at':'报告生成时间','provider':'地图服务','collection_mode':'采集方式','schedule_template':'方案版本','schedule_adjusted':'是否调整方案','schedule_window_confirmed':'采集窗口是否确认','day_type':'日期类型','directions':'方向','study_id':'研究编号','strategy':'路线策略','scope':'目标范围模式','window_minutes':'临时允许窗口（分钟）','query_order':'查询顺序','random_seed':'随机种子','order_algorithm':'排序算法','method':'报告计算口径'}
    for key in ['job_id','name','generated_at','provider','collection_mode','schedule_template','schedule_adjusted','schedule_window_confirmed','day_type','directions','study_id','strategy','scope','window_minutes','query_order','random_seed','order_algorithm','method']:
        value=report.get(key)
        if key=='provider': value=PROVIDER_LABELS.get(value,value)
        elif key=='directions' and isinstance(value,(list,tuple)): value='、'.join(DIRECTION_LABELS.get(x,x) for x in value)
        elif key in {'schedule_adjusted','schedule_window_confirmed'}: value='是' if value else '否'
        ws.append([safe_cell(ws,method_labels.get(key,key)),safe_cell(ws,value)])
    if report['provider']=='mock': ws.append(['数据用途','模拟测试数据，不用于实际交通研究'])
    wb.save(path)
