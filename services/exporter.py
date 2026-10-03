from datetime import datetime,timezone,timedelta
import math
import re
import json
import csv
import io
import zipfile
from pathlib import Path
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font,PatternFill
from utils.version import APP_VERSION
from services.research_plan import FINAL_TEMPLATE_VERSION,is_final_plan

TYPE_LABELS={'mock':'模拟测试数据','current_estimate':'当前路线预计耗时（非指定时刻预测）'}
DATA_USE_LABELS={'unmarked':'未标注','mock':'模拟测试','pilot':'真实试采','formal':'正式采集'}
DIRECTION_LABELS={'outbound':'去程','inbound':'返程','all':'未区分方向'}
STATUS_LABELS={'success':'成功','failed':'失败','pending':'待计算','partial':'部分成功'}
PROVIDER_LABELS={'amap':'高德地图','mock':'模拟服务'}
DAY_LABELS={'workday':'工作日','weekend':'周末','holiday':'节假日','custom':'自定义日期组'}
ROUTE_RULE_LABELS={'first':'首条路线','shortest_duration':'耗时最短路线'}
STRATEGY_LABELS={'32':'高德推荐（策略32）','33':'少收费（策略33）','34':'躲避拥堵（策略34）','35':'不走高速（策略35）','36':'少收费（策略36）','37':'大路优先（策略37）','38':'速度最快（策略38）'}
BOOL_FIELDS={'cache_hit','response_crossed_window','file_exists','valid','schedule_adjusted','schedule_window_confirmed','success'}
RAW_FIELDS={'raw_json','raw','request_params','columns_json','stats_json','polyline'}

# User-facing names in the audit package.  The internal names remain in the
# database and are included in the field dictionary so old tooling can map
# the package back to the original schema.
PACKAGE_FILES={
    'schedule.csv':'采集计划.csv','source_nodes.csv':'客源地信息.csv',
    'import_datasets.csv':'导入数据集.csv','import_rows.csv':'客源地导入行.csv',
    'destinations.csv':'重点村信息.csv','destination_import_rows.csv':'重点村导入行.csv',
    'observations.csv':'路线观测明细.csv','request_attempts.csv':'请求尝试记录.csv',
    'traffic_segments.csv':'分段路况.csv','quality_report.csv':'数据质量报告.csv',
    'raw_response_index.csv':'原始响应索引.csv','data_dictionary.csv':'字段说明.csv',
    'README.txt':'使用说明.txt'
}
PACKAGE_HEADER_LABELS={
    'planned_datetime':'计划采集时间（北京时间）','phase':'研究阶段','day_type':'日期类型','window_minutes':'允许窗口（分钟）','directions':'方向','notes':'备注','template_version':'计划模板版本','schedule_adjusted':'计划是否调整','schedule_window_confirmed':'采集窗口是否确认',
    'point_id':'客源地内部编号','name':'名称','point_name':'客源地名称','source_id':'客源地编号','source_name':'客源地名称','province':'省份','city':'城市','county':'县区','population':'城市人口（人）','population_year':'人口年份','population_scope':'人口口径','population_source':'人口来源','lon':'经度','lat':'纬度','longitude':'经度','latitude':'纬度','wlon':'WGS84经度','wlat':'WGS84纬度','wgs84_longitude':'WGS84经度','wgs84_latitude':'WGS84纬度','crs':'坐标系','coordinate_system':'坐标系','routing_lon':'查询经度','routing_lat':'查询纬度','routing_crs':'查询坐标系','valid':'是否有效','issues':'导入问题','row_no':'原始行号','raw':'原始行JSON','raw_json':'原始JSON',
    'dataset_id':'数据集编号','dataset_name':'数据集名称','file_name':'文件名','imported_at':'导入时间','file_crs':'文件坐标系','stats_json':'数据集统计JSON','normalized_id':'规范化编号','normalized_name':'规范化名称','wgs84_lon':'WGS84经度','wgs84_lat':'WGS84纬度','wgs84_latitude':'WGS84纬度',
    'id':'内部编号','target_id':'重点村编号','destination_id':'重点村编号','destination_name':'重点村名称','target_internal_id':'重点村内部编号','village_name':'村名','entrance_name':'导航入口名称','coordinate_source':'坐标来源','verification_status':'核验状态','quality_level':'坐标可信度等级','category':'等级','note':'备注','source_dataset_id':'来源数据集编号','source_row_no':'来源原始行号',
    'observation_id':'观测编号','task_id':'任务编号','job_id':'任务编号','point_pk':'客源地数据库内部编号','study_id':'研究编号','batch_id':'采集批次编号','departure':'计划采集时间（北京时间）','direction':'方向','date_group':'日期类型','origin_id':'本次请求起点编号','route_target_id':'本次请求终点编号','origin_lon':'逻辑起点经度','origin_lat':'逻辑起点纬度','origin_crs':'逻辑起点坐标系','target_name':'重点村名称','target_lon':'重点村经度','target_lat':'重点村纬度','target_crs':'重点村坐标系','twlon':'重点村WGS84经度','twlat':'重点村WGS84纬度','straight_km':'直线距离（千米）','distance_meters':'驾车距离（米）','duration_seconds':'预计驾车时间（秒）','traffic_type':'数据类型','provider':'地图服务','status':'状态','error':'错误信息','provider_error_code':'地图服务错误码','request_coords':'实际请求坐标','raw_status':'原始状态','calculated_at':'结果计算时间（UTC）','fetched_at':'收到响应时间（UTC）','cache_hit':'是否命中缓存','requested_at':'实际请求开始时间（UTC）','query_order':'查询排序键','last_requested_at':'最近请求开始时间（UTC）','attempts':'请求尝试次数','planned_datetime':'计划采集时间（北京时间）','window_end_datetime':'窗口结束时间（北京时间）','route_selection_rule':'路线选择规则','route_index':'路线序号','route_id':'路线标识','api_version':'接口版本','raw_response_path':'原始响应文件','traffic_coverage':'路况分段覆盖率（%）','request_started_at':'请求开始时间（UTC）','response_received_at':'收到响应时间（UTC）','saved_at':'保存时间（UTC）','request_delay_seconds':'相对计划时间偏移（秒）','response_crossed_window':'是否跨窗口响应','window_miss_reason':'错过窗口原因',
    'attempt_id':'请求尝试编号','started_at':'尝试开始时间（UTC）','finished_at':'尝试结束时间（UTC）','http_status':'HTTP状态码','business_status':'接口业务状态','retry_reason':'重试原因','success':'是否成功','rate_wait_seconds':'限流等待耗时（秒）','provider_duration_seconds':'接口响应耗时（秒）','raw_persistence_duration_seconds':'原始响应序列化与落盘耗时（秒）','segment_persistence_duration_seconds':'分段路况保存耗时（秒）','database_duration_seconds':'数据库提交耗时（秒）','persistence_duration_seconds':'持久化总耗时（秒）','total_duration_seconds':'单条总耗时（秒）','max_concurrency':'最大并发数','segment_index':'分段序号','status_raw':'路况原始值','status_normalized':'路况标准值','length_m':'分段长度（米）','integrity_status':'完整性状态','file_exists':'文件是否存在','file_size_bytes':'文件大小（字节）',
    'planned_observations':'计划观测数','successful_observations':'成功观测数','failed_observations':'失败观测数','pending_observations':'未采观测数','missed_window_observations':'错过窗口观测数','window_started_observations':'窗口内发起观测数','responses_crossed_window':'跨窗口响应数','request_attempts':'请求尝试数','traffic_observations':'有分段路况观测数','mean_traffic_coverage_percent':'平均路况覆盖率（%）','paper_valid_observations':'论文主表有效观测数','complete_rate_percent':'完整率（%）','data_use':'数据用途','configured_qps':'配置请求启动QPS','max_concurrency':'最大并发请求数','avg_provider_duration_seconds':'平均接口响应耗时（秒）','avg_raw_persistence_seconds':'平均原始响应落盘耗时（秒）','avg_segment_persistence_seconds':'平均分段路况保存耗时（秒）','avg_database_duration_seconds':'平均数据库提交耗时（秒）','avg_persistence_duration_seconds':'平均持久化耗时（秒）','avg_total_duration_seconds':'平均单条总耗时（秒）',
    'field':'内部字段','description':'字段说明'
}

def _clean(value):
    """Turn database sentinels into real blank cells without touching raw JSON."""
    return None if value is None or value == "'" else value

def _beijing(value):
    if value in (None,'','—',"'"): return None
    try:
        dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None).strftime('%Y-%m-%d %H:%M:%S')
    except (TypeError,ValueError): return value

def _epoch(value):
    if not value: return None
    try:
        dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError,ValueError): return None

def _number(value,integer=False):
    value=_clean(value)
    if value in (None,''): return None
    try:
        number=float(value)
        if not math.isfinite(number): return None
        return int(number) if integer else number
    except (TypeError,ValueError): return None

def _label_value(header,value):
    value=_clean(value)
    if value is None: return None
    if header in RAW_FIELDS: return value
    if header in ('direction',): return DIRECTION_LABELS.get(str(value),value)
    if header in ('provider',): return PROVIDER_LABELS.get(str(value),value)
    if header in ('traffic_type',): return TYPE_LABELS.get(str(value),value)
    if header in ('status','business_status'): return STATUS_LABELS.get(str(value),value)
    if header in ('verification_status','quality_level') and str(value)=='VERIFIED_MANUAL_LOCK': return '历史手工锁定且已核验'
    if header in ('day_type',): return DAY_LABELS.get(str(value),value)
    if header in ('route_selection_rule',): return ROUTE_RULE_LABELS.get(str(value),value)
    if header=='strategy': return STRATEGY_LABELS.get(str(value),value)
    if header=='data_use': return DATA_USE_LABELS.get(str(value),value)
    if header in BOOL_FIELDS:
        return '是' if str(value).lower() in {'1','true','yes','是'} else '否'
    if header=='directions' and isinstance(value,str):
        try: value=json.loads(value)
        except (TypeError,ValueError): pass
    if header=='directions' and isinstance(value,(list,tuple)): return '、'.join(DIRECTION_LABELS.get(str(x),str(x)) for x in value)
    return value

def _package_header(header):
    return PACKAGE_HEADER_LABELS.get(header, f'内部字段：{header}')
COLUMNS=[('point_id','客源点 ID'),('point_name','客源点名称'),('origin_lon','逻辑起点经度'),('origin_lat','逻辑起点纬度'),('origin_crs','逻辑起点坐标系'),('wlon','逻辑起点 WGS84 经度'),('wlat','逻辑起点 WGS84 纬度'),('target_name','目标地点'),('target_lon','目标经度'),('target_lat','目标纬度'),('target_crs','目标坐标系'),('straight_km','直线距离 km'),('distance_km','驾车距离 km'),('duration_min','驾车时间 min'),('date','实际采集日期'),('time','实际采集时间 UTC+8'),('traffic_type','数据类型'),('provider','地图 Provider'),('status','计算状态'),('error','错误信息'),('provider_error_code','地图服务错误码'),('window_miss_reason','错过窗口原因'),('calculated_at','结果提交时间 UTC'),('cache_hit','缓存命中'),('request_coords','实际请求坐标'),('fetched_at','原始获取时间 UTC'),('requested_at','实际请求开始时间 UTC'),('requested_at_local','实际请求开始时间 UTC+8'),('last_requested_at','最近请求开始时间 UTC'),('request_started_at','请求开始时间 UTC'),('response_received_at','收到响应时间 UTC'),('saved_at','保存时间 UTC'),('request_delay_seconds','相对计划时间延迟秒数'),('response_crossed_window','响应是否跨过窗口结束'),('attempts','请求尝试次数'),('query_order','查询排序键'),('direction','方向'),('task_id','任务 ID'),('study_id','研究编号'),('batch_id','采集批次 ID'),('observation_id','观测 ID'),('source_id','客源节点稳定 ID'),('destination_id','目的地稳定 ID'),('target_id','目标点内部 ID'),('origin_id','本次请求起点 ID'),('route_target_id','本次请求终点 ID'),('planned_datetime','计划时间 UTC+8'),('window_end_datetime','窗口结束 UTC+8'),('api_version','接口版本'),('strategy','路线策略'),('route_selection_rule','路线选择规则'),('route_index','路线序号'),('route_id','路线标识'),('traffic_coverage','路况分段覆盖率 %'),('raw_response_path','原始响应文件')]

def safe_cell(ws,value):
    value=_clean(value)
    if isinstance(value,(list,dict,tuple)):
        value=json.dumps(value,ensure_ascii=False)
    c=WriteOnlyCell(ws,value=value)
    # User strings are always text, never interpreted as Excel formulas.
    if isinstance(value,str): c.data_type='s'
    elif isinstance(value,float): c.number_format='0.00'
    return c

def sheet(wb,name,headers):
    ws=wb.create_sheet(name); ws.freeze_panes='A2'
    for i in range(len(headers)):
        from openpyxl.utils import get_column_letter
        ws.column_dimensions[get_column_letter(i+1)].width=24
    cells=[]
    for h in headers:
        c=safe_cell(ws,h); c.font=Font(color='FFFFFF',bold=True); c.fill=PatternFill('solid',fgColor='002FA7'); cells.append(c)
    ws.append(cells)
    return ws

def export_details(db,path,where,args,order):
    wb=Workbook(write_only=True); ws=sheet(wb,'路线明细',[label for _,label in COLUMNS]); count=0; page=1
    with db.connect() as conn:
        configs={r['id']:json.loads(r['config']) for r in conn.execute('SELECT id,config FROM jobs')}
        for row in conn.execute(f'SELECT * FROM results WHERE {where} ORDER BY {order}',args):
            if count and count%1000000==0:
                page+=1; ws=sheet(wb,f'路线明细{page}',[label for _,label in COLUMNS])
            r=dict(row); r['task_id']=r['job_id']; cfg=configs[r['job_id']]; stamp=r.get('requested_at') or r.get('departure'); dt=datetime.fromisoformat(stamp).astimezone(timezone(timedelta(hours=8))) if stamp else None
            # Configs are loaded as JSON above; keep the route policy in the
            # exported row even when the legacy task did not store it.
            r.update(distance_km=r['distance_meters']/1000 if r['distance_meters'] is not None else None,duration_min=r['duration_seconds']/60 if r['duration_seconds'] is not None else None,date=dt.date() if dt else None,time=dt.time().replace(tzinfo=None) if dt else None,requested_at_local=dt.astimezone(timezone(timedelta(hours=8))).isoformat() if dt else None,traffic_type=TYPE_LABELS.get(r['traffic_type'],r['traffic_type']),provider=PROVIDER_LABELS.get(r.get('provider'),r.get('provider')),status=STATUS_LABELS.get(r.get('status'),r.get('status')),direction=DIRECTION_LABELS.get(r.get('direction'),r.get('direction')),cache_hit=_label_value('cache_hit',r.get('cache_hit')),response_crossed_window=_label_value('response_crossed_window',r.get('response_crossed_window')),route_selection_rule=ROUTE_RULE_LABELS.get(r.get('route_selection_rule'),r.get('route_selection_rule')),strategy=cfg.get('strategy',''),api_version=r.get('api_version') or cfg.get('api_version',''))
            ws.append([safe_cell(ws,r.get(k)) for k,_ in COLUMNS]); count+=1
    wb.save(path)

def summaries(db,jid,departure,top=3,limit=None,offset=0,direction=''):
    # Results include only candidates in saving mode. Nearest straight target is computed from full snapshot separately.
    import json
    from utils.geo import haversine
    cfg=json.loads(db.one('SELECT config FROM jobs WHERE id=?',(jid,))['config'])
    # A summary row always represents one origin *and one direction*.  The
    # old implementation used an implicit ``all`` direction and counted the
    # two legs as two successful targets; that made a two-village trial look
    # like four destinations.  Keeping the direction in the grouping also
    # makes the Top ranking reproducible for outbound and inbound separately.
    sql='SELECT DISTINCT point_pk,point_id,point_name,wlon,wlat,direction FROM results WHERE job_id=? AND departure=?'
    base_args=[jid,departure]
    if direction:
        sql+=' AND direction=?'; base_args.append(direction)
    sql+=' ORDER BY point_pk'
    if limit is not None: sql+=' LIMIT ? OFFSET ?'
    with db.connect() as conn:
        cursor=conn.execute(sql,(*base_args,limit,offset) if limit is not None else tuple(base_args))
        for p in cursor:
            row_direction=direction or p['direction'] or 'outbound'
            route_args=[jid,p['point_pk'],departure,row_direction]
            direction_sql=' AND direction=?'
            routes=[dict(r) for r in conn.execute("SELECT target_id,target_name,distance_meters,duration_seconds,traffic_type FROM results WHERE job_id=? AND point_pk=? AND departure=? AND status='success'"+direction_sql+" ORDER BY duration_seconds,target_id",tuple(route_args))]
            if cfg.get('institution_summary'):
                targets={t['id']:t for t in cfg['targets']}; unique={}
                for r in routes:
                    t=targets[r['target_id']]; ident=t.get('institution_id','').strip()
                    key=('institution',ident) if ident else ('entrance',t['id'])
                    if key not in unique:
                        r['entrance_name']=r['target_name']
                        r['target_name']=t.get('institution_name') or r['target_name']
                        unique[key]=r
                routes=list(unique.values())
            nearest=min(cfg['targets'],key=lambda t:haversine((p['wlon'],p['wlat']),(t['wlon'],t['wlat']))) if cfg.get('targets') else {'name':'—'}
            # Counts are distinct logical target IDs, never route rows.  The
            # hour columns are deliberately explicit: these are target
            # counts, not the paper's population-weighted 60/120/180/240
            # minute market indicators.
            distinct={str(r.get('target_id')) for r in routes if r.get('target_id') is not None}
            counts={str(h):len({str(r.get('target_id')) for r in routes if r.get('duration_seconds') is not None and r['duration_seconds']<=h*3600}) for h in (1,2,3,4)}
            scope=(f"候选入口预筛选结果；本任务附近目标预筛选（{len(cfg.get('targets',[]))}个，非研究区全量）" if cfg.get('mode')=='saving' else f"本任务所选目标范围（{len(cfg.get('targets',[]))}个，非研究区全量94村）")
            if cfg.get('institution_summary'): scope+='；按机构 ID 去重，取成功入口最短耗时'
            yield dict(point_id=p['point_id'],point_name=p['point_name'],nearest_straight=nearest['name'],top=routes[:top],reachable=counts,departure=departure,direction=row_direction,scope=scope,success_routes=len(routes),distinct_targets=len(distinct),success_targets=len(distinct))

def export_summary(db,path,jid,departure,direction=''):
    wb=Workbook(write_only=True)
    ws=sheet(wb,'起点汇总',['客源地编号','客源地名称','方向','直线距离最近重点村','驾车耗时最短重点村','驾车距离（千米）','预计驾车时间（分钟）','排名第2','排名第3','1小时内成功重点村数','2小时内成功重点村数','3小时内成功重点村数','4小时内成功重点村数','计划采集时刻（北京时间）','目标范围说明','数据类型','成功路线数','不同重点村数'])
    for s in summaries(db,jid,departure,direction=direction):
        top=s['top']; best=top[0] if top else {}
        values=[s['point_id'],s['point_name'],DIRECTION_LABELS.get(s['direction'],s['direction']),s['nearest_straight'],best.get('target_name'),best.get('distance_meters',0)/1000 if best else None,best.get('duration_seconds',0)/60 if best else None,top[1]['target_name'] if len(top)>1 else None,top[2]['target_name'] if len(top)>2 else None,s['reachable']['1'],s['reachable']['2'],s['reachable']['3'],s['reachable']['4'],departure,s['scope'],TYPE_LABELS.get(best.get('traffic_type')),s['success_routes'],s['distinct_targets']]
        ws.append([safe_cell(ws,x) for x in values])
    wb.save(path)


PAPER_COLUMNS=[
    # The paper sheet is intentionally the smallest useful analytical long
    # table.  Provenance, filtering decisions, static coordinates and API
    # details remain in the quality/audit and static tables, joined by the
    # stable observation/source/destination identifiers when needed.
    ('observation_id','观测编号'),('source_id','客源地编号'),('source_name','客源地名称'),
    ('destination_id','重点村编号'),('destination_name','重点村名称'),('planned_date','计划日期'),
    ('scenario_code','研究情景'),('direction','方向'),('population','城市人口（人）'),
    ('duration_min','预计驾车时间（分钟）'),('distance_km','驾车距离（千米）'),('straight_km','直线距离（千米）')
]

# These columns remain available in the quality workbook.  They are not
# analytical measures and therefore are intentionally absent from
# ``论文分析主表``.  The audit detail table uses stable observation IDs so a
# reviewer can join them back without copying audit metadata into calculations.
AUDIT_COLUMNS=[
    ('task_id','任务编号'),('observation_id','观测编号'),('batch_id','采集批次编号'),('data_use','数据用途'),('research_phase','研究阶段'),('day_type','日期类型'),
    ('planned_offset_seconds','相对计划时间偏移（秒）'),('response_crossed_window','是否跨窗口响应'),('traffic_type','数据类型'),
    ('population_eligibility','人口加权指标资格'),('population_source','人口来源'),('population_unit','人口单位'),
    ('quality_result','质量检查结果'),('formal_status','正式分析纳入状态'),('note','说明'),
    ('plan_version','方案版本'),('schedule_adjusted','是否调整方案'),('coordinate_source','目的地坐标来源'),
    ('verification_status','目的地核验状态'),('formal_plan_eligibility','正式计划资格')
]

PAPER_FIELD_DOCS=[
    ('任务编号','job_id','任务的稳定编号；用于追溯导出来源','文本','任务快照','不缺失时保留'),
    ('观测编号','observation_id','单条客源地—重点村—时间—方向观测的稳定编号','文本','路线结果表','缺失则不能纳入主表'),
    ('采集批次编号','batch_id','同一计划时点下的采集批次编号','文本','路线结果表','空值保持空白'),
    ('数据用途','config.data_use','模拟测试、真实试采、正式采集或未标注','类别','任务配置快照','旧任务默认未标注'),
    ('方案版本/是否调整方案','config.schedule_template/schedule_adjusted','计划模板版本及用户是否改动锁定日历','文本/类别','任务配置快照','未标注或旧任务保持空白/否'),
    ('研究阶段','departure_metadata.phase','计划所属阶段','文本','计划快照','立即采集使用“立即采集”'),
    ('日期类型','departure_metadata.day_type','工作日、周末、节假日或自定义日期组','类别','计划快照','无法判断时为自定义日期组'),
    ('计划采集日期/时间','planned_datetime/departure','计划发起时间（北京时间），不是实际请求时间','日期/时间','计划快照','缺失保持空白'),
    ('方向','direction','去程为客源地→重点村，返程为重点村→客源地；逻辑ID不交换','类别','路线结果表','未知方向不纳入主表'),
    ('客源地编号/名称/省份','source_id/source_snapshot','逻辑客源单元及其任务保存快照','文本','客源地快照','不联网补全'),
    ('城市人口（人）','source_snapshot.population','人口权重；只用于后续人口加权指标','数值（人）','客源地快照','缺失不否定路线，但不能参与人口加权指标'),
    ('人口年份/单位/口径/来源','population_year/population_unit/population_scope/population_source','人口统计年份、单位、统计范围和来源；来源缺失不补写','年份/文本','客源地快照','缺失保持空白，不自动猜测'),
    ('人口加权指标资格','derived','人口缺失时路线仍有效，但不得直接参加PWT、M和MP','类别','导出判定','人口缺失标记为否'),
    ('重点村行政字段','targets snapshot','目的地任务快照中的省、市、县区、乡镇和村名','文本','重点村快照及原始导入行','不得联网补全'),
    ('坐标可信度等级','category/quality_level/verification_status','目的地坐标来源或核验等级；历史锁定点保留其历史来源','文本','重点村快照','缺失保持空白'),
    ('路线坐标及来源','source_snapshot/targets','客源地和重点村的任务快照坐标、坐标系及目的地来源；不重复转换GCJ-02','数值/文本','任务快照','缺失保持空白，不联网补全'),
    ('预计驾车时间（分钟）','duration_seconds/60','地图服务返回的预计驾车耗时，不是实车测量时间','数值（分钟）','路线接口结果','不能解析或无效时排除'),
    ('驾车距离/直线距离','distance_meters/straight_km','道路距离和Haversine直线距离','数值（千米）','路线结果/计算结果','不把单位写入数值单元格'),
    ('实际请求/响应时间','requested_at,response_received_at','真实请求开始和收到响应的北京时间，保留秒','时间','请求结果表','计划时间不替代实际请求时间'),
    ('相对计划时间偏移','request_delay_seconds','请求开始相对计划时间的秒数','数值（秒）','采集结果','缺失保持空白'),
    ('是否跨窗口响应','response_crossed_window','请求在窗口内发出、窗口后返回时标为“是”；按现有规则保留','类别','采集结果','空值保持空白'),
    ('地图服务/数据类型','provider/traffic_type','高德地图及当前路线预计耗时等类别','类别','任务配置/接口结果','模拟数据不纳入论文主表'),
    ('路线策略/选择规则','strategy/route_selection_rule','接口策略代码和选取路线规则','文本','任务配置/接口结果','不改写原始API参数'),
    ('质量检查结果','derived','路线是否通过明确的基础质量规则','类别','导出判定','人口缺失不影响路线有效性'),
    ('正式分析纳入状态','derived','是否可作为正式研究分析记录；试采和未标注不自动成为正式样本','类别','导出判定','需结合任务用途及完整性报告'),
    ('正式计划资格','derived','是否严格符合最终7日、10:00、仅去程方案；不等于采集已完成','类别','方案快照/导出判定','非最终方案标记为否'),
    ('说明','derived','历史快照、人口缺失、超窗响应、附近目标预筛选等限制','文本','导出判定','不把缺测解释为不可达'),
]

def _target_lookup(cfg):
    by_internal={}; by_stable={}
    for target in cfg.get('targets') or []:
        if target.get('id') is not None: by_internal[str(target.get('id'))]=target
        for key in ('target_id','destination_id','id'):
            if target.get(key) not in (None,''): by_stable[str(target.get(key))]=target
    return by_internal,by_stable

def _source_lookup(cfg):
    by_stable={}; by_internal={}
    for source in cfg.get('source_snapshot') or []:
        for key in ('source_id','point_id','id'):
            if source.get(key) not in (None,''):
                by_stable.setdefault(str(source.get(key)),source)
        if source.get('id') is not None: by_internal[str(source.get('id'))]=source
    return by_internal,by_stable

def _snapshot_value(item,*keys):
    for key in keys:
        value=item.get(key) if isinstance(item,dict) else None
        if value not in (None,'',"'"): return value
    raw=item.get('raw') if isinstance(item,dict) else None
    if raw:
        try:
            parsed=json.loads(raw) if isinstance(raw,str) else raw
            if isinstance(parsed,dict):
                for key in keys:
                    value=parsed.get(key)
                    if value not in (None,'',"'"): return value
        except (TypeError,ValueError):
            pass
    return None

def _window_bounds(row,cfg):
    planned=row.get('planned_datetime') or row.get('departure')
    start=_epoch(planned)
    end=_epoch(row.get('window_end_datetime'))
    if end is None and start is not None:
        meta=(cfg.get('departure_metadata') or {}).get(planned or '',{})
        end=start+int(meta.get('window_minutes',cfg.get('window_minutes',20)))*60
    return start,end

def _classify_paper_rows(job,cfg,rows):
    """Classify route rows without changing the stored observations."""
    source_internal,source_stable=_source_lookup(cfg); target_internal,target_stable=_target_lookup(cfg)
    seen=set(); valid=[]; excluded=[]; details=[]
    scheduled=cfg.get('collection_mode')=='scheduled'
    data_use=cfg.get('data_use','unmarked') or 'unmarked'
    final_plan=cfg.get('schedule_template')==FINAL_TEMPLATE_VERSION and is_final_plan(cfg.get('schedule') or [])
    complete_final_sample=final_plan and job.get('status')=='completed' and bool(rows) and len(rows)==int(job.get('total') or len(rows)) and all(r.get('status')=='success' for r in rows)
    for row in rows:
        r=dict(row); reasons=[]; notes=[]
        source_id=str(r.get('source_id') or r.get('point_id') or '')
        destination_id=str(r.get('destination_id') or '')
        target=target_internal.get(str(r.get('target_id'))) or target_stable.get(destination_id) or {}
        source=source_stable.get(source_id) or source_internal.get(str(r.get('point_pk'))) or {}
        source_id=str(source_id or source.get('source_id') or source.get('point_id') or '')
        if not source_id: reasons.append('缺少稳定客源地编号')
        if not destination_id:
            destination_id=str(target.get('target_id') or r.get('target_id') or '')
        if not destination_id: reasons.append('缺少稳定重点村编号')
        if r.get('direction') not in {'outbound','inbound'}: reasons.append('方向缺失或未知')
        if r.get('provider')!='amap' or r.get('traffic_type')!='current_estimate': reasons.append('不是高德真实当前路线观测')
        if r.get('status')!='success': reasons.append('请求未成功')
        distance=r.get('distance_meters'); duration=r.get('duration_seconds')
        try:
            if distance is None or not math.isfinite(float(distance)) or float(distance)<0: reasons.append('驾车距离缺失或无效')
        except (TypeError,ValueError): reasons.append('驾车距离缺失或无效')
        try:
            if duration is None or not math.isfinite(float(duration)) or float(duration)<0: reasons.append('驾车耗时缺失或无效')
        except (TypeError,ValueError): reasons.append('驾车耗时缺失或无效')
        actual=r.get('requested_at') or r.get('request_started_at') or r.get('last_requested_at')
        if not actual: reasons.append('缺少实际请求时间')
        if bool(r.get('cache_hit')): reasons.append('使用了历史缓存')
        key=(source_id,destination_id,r.get('departure') or r.get('planned_datetime') or '',r.get('direction') or '')
        if key in seen: reasons.append('同一观测意外重复')
        seen.add(key)
        if scheduled:
            actual_epoch=_epoch(actual); start,end=_window_bounds(r,cfg)
            if actual_epoch is None: reasons.append('实际请求时间无法解析')
            elif start is None or end is None or not (start<=actual_epoch<end): reasons.append('定时请求未在允许窗口内启动')
            if r.get('response_crossed_window'): notes.append('请求在窗口内发出但响应跨窗口返回，按规则保留')
        population_value=_number(_snapshot_value(source,'population','城市人口（人）'))
        if population_value is None: notes.append('人口缺失：路线有效，但不能参与人口加权市场指标')
        if cfg.get('mode')=='saving': notes.append('附近目标预筛选：不代表全部重点村')
        if r.get('traffic_coverage') is not None and float(r.get('traffic_coverage') or 0)>=100:
            notes.append('路况分段覆盖率不等于旅行时间准确率')
        if reasons:
            excluded.append(dict(observation_id=r.get('observation_id'),source_id=source_id,destination_id=destination_id,direction=DIRECTION_LABELS.get(r.get('direction'),r.get('direction')),planned_datetime=r.get('planned_datetime') or r.get('departure'),reason='；'.join(dict.fromkeys(reasons))))
            continue
        meta=(cfg.get('departure_metadata') or {}).get(r.get('departure') or r.get('planned_datetime') or '',{})
        planned=r.get('planned_datetime') or r.get('departure')
        phase=meta.get('phase') or ('立即采集' if cfg.get('collection_mode')=='immediate' else '')
        day_type=DAY_LABELS.get(meta.get('day_type',cfg.get('day_type','custom')),meta.get('day_type',cfg.get('day_type','custom')))
        if data_use=='formal' and complete_final_sample: formal_status='纳入正式分析（最终方案且本任务完整）'
        elif data_use=='formal' and final_plan: formal_status='正式采集未完整，不自动纳入完整样本'
        elif data_use=='formal': formal_status='正式用途但非最终锁定方案，不自动纳入新版正式分析'
        elif data_use=='pilot': formal_status='试采验证，不纳入正式分析'
        elif data_use=='mock': formal_status='模拟数据，不纳入正式分析'
        else: formal_status='未标注，不自动纳入正式分析'
        if job.get('status') not in {'completed','partial','failed'}: formal_status=formal_status+'；阶段性导出，不代表正式样本完整'
        target_name=_snapshot_value(target,'village_name','村名','name','重点村名称') or r.get('target_name')
        confidence=_snapshot_value(target,'quality_level','坐标可信度等级','category','verification_status','核验状态') or ''
        if confidence=='VERIFIED_MANUAL_LOCK': confidence='历史手工锁定且已核验'
        planned_date=str(planned or '')[:10]
        if planned_date in {'2026-10-01','2026-10-02','2026-10-03'}: scenario_code={'2026-10-01':'D1','2026-10-02':'D2','2026-10-03':'D3'}[planned_date]
        elif planned_date in {'2026-10-17','2026-10-18','2026-10-24','2026-10-25'}: scenario_code='WE'
        else: scenario_code=''
        weekend_group='普通周末1' if planned_date in {'2026-10-17','2026-10-18'} else ('普通周末2' if planned_date in {'2026-10-24','2026-10-25'} else '')
        population_eligible='是' if population_value is not None else '否'
        target_lon=_number(_snapshot_value(target,'lon','longitude','经度'))
        target_lat=_number(_snapshot_value(target,'lat','latitude','纬度'))
        source_lon=_number(_snapshot_value(source,'lon','longitude','经度'))
        source_lat=_number(_snapshot_value(source,'lat','latitude','纬度'))
        formal_plan_eligibility='是' if final_plan else '否'
        row_out={
            'task_id':job.get('id'),'observation_id':r.get('observation_id'),'batch_id':r.get('batch_id'),'data_use':DATA_USE_LABELS.get(data_use,'未标注'),'plan_version':cfg.get('schedule_template') or '','schedule_adjusted':'是' if cfg.get('schedule_adjusted') else '否',
            'research_phase':phase,'day_type':day_type,'planned_date':str(planned or '')[:10] or None,'planned_time':str(planned or '')[11:19] or None,'direction':DIRECTION_LABELS.get(r.get('direction'),r.get('direction')),
            'scenario_code':scenario_code,'weekend_group':weekend_group,'source_id':source_id,'source_name':_snapshot_value(source,'source_name','name','客源地名称') or r.get('point_name'),'source_province':_snapshot_value(source,'province','省份','客源省份'),'population':population_value,'population_year':_number(_snapshot_value(source,'population_year','人口年份'),integer=True),'population_unit':('人' if population_value is not None else None),'population_scope':_snapshot_value(source,'population_scope','人口口径'),'population_source':_snapshot_value(source,'population_source','人口来源'),'population_eligibility':population_eligible,'source_lon':source_lon,'source_lat':source_lat,'source_crs':_snapshot_value(source,'crs','coordinate_system','路线查询坐标系'),
            'destination_id':destination_id,'destination_name':target_name,'destination_province':_snapshot_value(target,'province','省份','目的地省份'),'destination_city':_snapshot_value(target,'city','城市','目的地城市'),'destination_county':_snapshot_value(target,'county','县区','目的地县区'),'destination_town':_snapshot_value(target,'town','township','乡镇','乡镇街道'),'coordinate_confidence':confidence,'destination_lon':target_lon,'destination_lat':target_lat,'destination_crs':_snapshot_value(target,'crs','coordinate_system','路线查询坐标系'),'coordinate_source':_snapshot_value(target,'coordinate_source','坐标来源'),'verification_status':_snapshot_value(target,'verification_status','核验状态'),
            'duration_min':float(duration)/60 if duration is not None else None,'distance_km':float(distance)/1000 if distance is not None else None,'straight_km':r.get('straight_km'),
            'requested_at_bj':_beijing(actual),'response_at_bj':_beijing(r.get('response_received_at') or r.get('fetched_at')),'planned_offset_seconds':r.get('request_delay_seconds'),'response_crossed_window':'是' if r.get('response_crossed_window') else '否',
            'provider':PROVIDER_LABELS.get(r.get('provider'),r.get('provider')),'traffic_type':TYPE_LABELS.get(r.get('traffic_type'),r.get('traffic_type')),'strategy':STRATEGY_LABELS.get(str(cfg.get('strategy')),cfg.get('strategy') or ''),'route_selection_rule':ROUTE_RULE_LABELS.get(r.get('route_selection_rule'),r.get('route_selection_rule')),
            'quality_result':'通过','formal_plan_eligibility':formal_plan_eligibility,'formal_status':formal_status,'note':'；'.join(dict.fromkeys(notes))
        }
        valid.append(row_out)
    return valid,excluded

def _paper_context(db,jid):
    job=db.one('SELECT * FROM jobs WHERE id=?',(jid,))
    if not job: raise ValueError('任务不存在')
    cfg=json.loads(job.get('config') or '{}'); rows=db.rows('SELECT * FROM results WHERE job_id=? ORDER BY departure,query_order,id',(jid,))
    valid,excluded=_classify_paper_rows(job,cfg,rows)
    attempts=db.rows('SELECT * FROM request_attempts WHERE job_id=? ORDER BY id',(jid,))
    def _attempt_avg(field):
        values=[]
        for attempt in attempts:
            value=_number(attempt.get(field))
            if value is not None: values.append(value)
        return (sum(values)/len(values)) if values else None
    performance={
        'attempts':len(attempts),
        'avg_rate_wait_seconds':_attempt_avg('rate_wait_seconds'),
        'avg_provider_seconds':_attempt_avg('provider_duration_seconds'),
        'avg_raw_persistence_seconds':_attempt_avg('raw_persistence_duration_seconds'),
        'avg_segment_persistence_seconds':_attempt_avg('segment_persistence_duration_seconds'),
        'avg_database_seconds':_attempt_avg('database_duration_seconds'),
        'avg_persistence_seconds':_attempt_avg('persistence_duration_seconds'),
        'avg_total_seconds':_attempt_avg('total_duration_seconds'),
        'max_total_seconds':max((_number(a.get('total_duration_seconds')) or 0 for a in attempts),default=None),
        'max_concurrency':max((_number(a.get('max_concurrency'),integer=True) or 0 for a in attempts),default=None),
    }
    planned=int(job.get('total') or len(rows)); success=sum(1 for r in rows if r.get('status')=='success'); failed=sum(1 for r in rows if r.get('status')=='failed'); pending=max(0,planned-success-failed)
    actuals=[r.get('requested_at') or r.get('request_started_at') for r in rows if r.get('requested_at') or r.get('request_started_at')]
    responses=[r.get('response_received_at') or r.get('fetched_at') for r in rows if r.get('response_received_at') or r.get('fetched_at')]
    cache_hits=sum(bool(r.get('cache_hit')) for r in rows); crossed=sum(bool(r.get('response_crossed_window')) for r in rows); pop_missing=sum(1 for r in valid if r.get('population') in (None,''))
    batches={}
    valid_ids={str(r.get('observation_id')) for r in valid if r.get('observation_id') not in (None,'')}
    for row in rows:
        key=(row.get('departure') or '',row.get('direction') or '')
        q=batches.setdefault(key,[0,0,0]); q[0]+=1; q[1]+=int(row.get('status')=='success'); q[2]+=int(str(row.get('observation_id')) in valid_ids)
    pair_keys={}
    for row in rows:
        key=(row.get('source_id') or row.get('point_id'),row.get('destination_id') or row.get('target_id'),row.get('departure') or row.get('planned_datetime'))
        pair_keys.setdefault(key,set()).add(row.get('direction'))
    paired=sum(v=={'outbound','inbound'} for v in pair_keys.values())
    data_use=cfg.get('data_use','unmarked') or 'unmarked'; purpose=DATA_USE_LABELS.get(data_use,'未标注')
    final_plan=cfg.get('schedule_template')==FINAL_TEMPLATE_VERSION and is_final_plan(cfg.get('schedule') or [])
    if data_use=='pilot': conclusion=f'本次{len(valid)}条高德真实试采观测满足基础路线质量规则，可用于试采验证；因属于真实试采，不纳入论文正式情景比较。'
    elif data_use=='formal' and job.get('status')=='completed': conclusion=f'本次导出包含{len(valid)}条通过基础路线质量规则的观测；正式分析是否可计算某项指标，仍需检查重复观测、基准和人口字段。'
    elif data_use=='formal': conclusion=f'本次正式采集任务尚未形成完整样本；当前{len(valid)}条通过基础路线质量规则的观测仅作为阶段性结果，不自动纳入完整情景比较。'
    elif data_use=='mock': conclusion='本次为模拟测试，不能用于论文交通研究。'
    else: conclusion=f'本次任务用途为“{purpose}”；导出不自动把未标注或阶段性结果认定为正式研究样本。'
    if job.get('status') not in {'completed','partial','failed'}: conclusion+=' 当前任务尚未结束，属于阶段性导出。'
    if final_plan and not cfg.get('schedule_window_confirmed'): conclusion+=' 最终方案的允许采集窗口尚未确认。'
    missing_key=sum(1 for row in excluded if any(token in str(row.get('reason') or '') for token in ('编号','实际请求时间','距离','耗时','窗口')))
    manual_lock=sum(1 for row in valid if row.get('coordinate_confidence')=='历史手工锁定且已核验')
    bidirectional_applicable=any(len({direction for (dep,direction) in batches if dep==departure})==2 for departure in {dep for dep,_direction in batches})
    context=dict(job=job,cfg=cfg,rows=rows,valid=valid,excluded=excluded,attempts=attempts,performance=performance,planned=planned,success=success,failed=failed,pending=pending,actual_start=_beijing(min(actuals,key=lambda x:_epoch(x) or float('inf')) if actuals else None),actual_end=_beijing(max(responses or actuals,key=lambda x:_epoch(x) or 0) if (responses or actuals) else None),cache_hits=cache_hits,crossed=crossed,pop_missing=pop_missing,missing_key=missing_key,manual_lock=manual_lock,batches=batches,paired=paired,bidirectional_applicable=bidirectional_applicable,final_plan=final_plan,data_use=purpose,conclusion=conclusion)
    return context

def _quality_sheet(wb,context,title='数据质量说明'):
    c=context; job=c['job']; cfg=c['cfg']
    ws=sheet(wb,title,['项目','值','说明'])
    selected_targets=len(cfg.get('targets') or [])
    if c['data_use']=='真实试采': formal_summary='试采验证，不纳入正式分析'
    elif c['data_use']=='模拟测试': formal_summary='模拟测试，不纳入正式分析'
    elif c['data_use']=='未标注': formal_summary='未标注，不自动纳入正式分析'
    elif c.get('final_plan') and job.get('status')=='completed' and not c.get('excluded') and c.get('planned')==len(c.get('valid') or []): formal_summary='纳入正式分析（最终方案且本任务完整）'
    elif c.get('final_plan'): formal_summary='符合最终方案日历，但样本/质量尚未完整，不自动纳入完整样本'
    else: formal_summary='正式用途但非最终锁定方案，不自动纳入新版正式分析'
    valid_rows=c.get('valid') or []
    def _distinct(key, translate=None, limit=12):
        values=[]
        for item in valid_rows:
            value=item.get(key)
            if translate:
                value=translate(value)
            value=_clean(value)
            if value in (None,''):
                continue
            text=str(value)
            if text not in values:
                values.append(text)
        if not values:
            return '无可用值'
        if len(values)>limit:
            return '、'.join(values[:limit])+f'等{len(values)}项'
        return '、'.join(values)

    offsets=[]
    for item in valid_rows:
        value=_number(item.get('planned_offset_seconds'))
        if value is not None:
            offsets.append(value)
    offset_summary=(f'范围 {min(offsets):g}—{max(offsets):g} 秒；有效记录 {len(offsets)} 条'
                    if offsets else '无可解析的有效记录')
    crossed_detail=f"是 {sum(1 for item in valid_rows if item.get('response_crossed_window')=='是')} 条；否 {sum(1 for item in valid_rows if item.get('response_crossed_window')=='否')} 条"
    population_eligible_detail=f"是 {sum(1 for item in valid_rows if item.get('population_eligibility')=='是')} 条；否 {sum(1 for item in valid_rows if item.get('population_eligibility')=='否')} 条"
    population_source_detail=_distinct('population_source')
    if population_source_detail=='无可用值':
        population_source_detail='任务客源快照未单列人口来源字段；不在导出中猜测补写'
    quality_detail=f"通过 {len(valid_rows)} 条；排除 {len(c.get('excluded') or [])} 条"
    status_detail=_distinct('formal_status')
    note_detail=_distinct('note', limit=5)
    records=[
        ('任务名称',job.get('name'),'任务创建时的名称快照'),('任务编号',job.get('id'),'用于追溯，不是数据库内部自增ID'),('数据用途',c['data_use'],'未标注任务不得根据名称或样本量猜测用途'),('方案版本',cfg.get('schedule_template') or '旧任务/自定义','任务创建时的日历版本快照'),('是否调整方案','是' if cfg.get('schedule_adjusted') else '否','修改日期、时点或方向后不再称为锁定方案'),('最终方案资格','是' if c.get('final_plan') else '否','须为7日、每天10:00、仅去程；不等于任务已经完整'),('采集窗口确认','是' if cfg.get('schedule_window_confirmed') else '待确认','最终Word未规定允许窗口分钟数'),
        ('研究阶段',_distinct('research_phase'),'计划快照中的阶段分类；不是路线观测值'),('日期类型',_distinct('day_type'),'计划快照中的日期分类；正式情景仍以计划日期/研究情景为准'),('相对计划时间偏移（秒）',offset_summary,'质量/时窗审核字段，不用于替代计划采集时间'),('是否跨窗口响应',crossed_detail,'窗口内发起、窗口后返回按规则保留并标注'),('数据类型',_distinct('traffic_type'),'真实高德当前路线预计耗时才可进入主表；模拟/其他类型会被排除'),('人口加权指标资格',population_eligible_detail,'人口缺失不否定路线有效性，但阻止该记录直接参加人口加权指标'),('人口来源',population_source_detail,'人口来源只按任务快照记录；缺失保持空白'),('人口单位',_distinct('population_unit'),'数值人口在主表保留；单位完整性在质量报告核查'),('质量检查结果',quality_detail,'基础路线质量判定；排除记录及原因见下方明细'),('正式分析纳入状态',status_detail,'路线有效、正式研究纳入和指标可计算条件是三件不同的事'),('说明',note_detail,'逐条说明保存在下方质量审核明细；排除原因见排除明细'),('目的地坐标来源',_distinct('coordinate_source'),'来自任务保存的目的地快照，不联网补全'),('目的地核验状态',_distinct('verification_status',lambda value:_label_value('verification_status',value)),'历史手工锁定点保留历史来源标记'),('正式计划资格',_distinct('formal_plan_eligibility'),'是否严格符合最终日历；不等于本次采集已完成'),
        ('数据来源','高德地图真实接口' if cfg.get('provider')=='amap' else '模拟服务/未标注服务', '模拟服务不属于真实交通观测'),('接口版本',cfg.get('api_version'),'任务配置快照'),('软件版本',APP_VERSION,'本次导出程序版本'),('路线策略',cfg.get('strategy'),'保留任务快照'),
        ('请求启动QPS（任务创建快照）',(cfg.get('execution_limits') or {}).get('qps', (cfg.get('capacity_estimate') or {}).get('configured_qps')),'仅为本软件启动限速，不代表高德账号授权额度'),('最大并发请求数（任务创建快照）',(cfg.get('execution_limits') or {}).get('max_concurrency',(cfg.get('capacity_estimate') or {}).get('max_concurrency')),'同时等待响应的上限，与QPS分开控制'),('纯限流理论下限（分钟）',(cfg.get('capacity_estimate') or {}).get('minimum_minutes'),'仅为请求启动速率下限，不是完成保证'),('离线模型估计耗时（分钟）',(cfg.get('capacity_estimate') or {}).get('estimated_minutes',(cfg.get('capacity_estimate') or {}).get('planning_minutes')),'结合假设响应耗时和并发的离线模型，不含真实波动'),('性能证据',(cfg.get('capacity_estimate') or {}).get('performance_evidence','离线模型（非真实验证）'),'不得据此宣称正式采集性能已验证'),('平均接口耗时（秒）',c['performance'].get('avg_provider_seconds'),'来自本任务请求尝试的单调时钟计时'),('平均原始响应落盘耗时（秒）',c['performance'].get('avg_raw_persistence_seconds'),'包含JSON序列化、写入和fsync；不含接口耗时'),('平均分段路况保存耗时（秒）',c['performance'].get('avg_segment_persistence_seconds'),'仅对有分段记录的保存循环计时'),('平均数据库提交耗时（秒）',c['performance'].get('avg_database_seconds'),'本地数据库事务耗时，受环境影响'),('平均单条总耗时（秒）',c['performance'].get('avg_total_seconds'),'单调时钟端到端参考，不等于请求发放间隔'),('最大单条总耗时（秒）',c['performance'].get('max_total_seconds'),'用于识别长尾，不代表未来上界'),
        ('计划观测数',c['planned'],'来自任务计划，不把部分采集标为全研究样本完整'),('成功数',c['success'],'数据库状态为成功的路线数'),('失败数',c['failed'],'失败或缺失不解释为不可达'),('未采数',c['pending'],'计划数减已处理数'),('主表纳入数',len(c['valid']),'通过明确路线质量规则的有效观测'),('排除数',len(c['excluded']),'排除编号和原因见本表下方明细'),
        ('缓存命中数',c['cache_hits'],'主表规则不使用历史缓存'),('重试次数',sum(1 for _ in c['attempts']),'请求尝试记录数，不等于路线数'),('跨窗口响应数',c['crossed'],'窗口内发起、窗口后返回按规则保留并标注'),('人口缺失记录数',c['pop_missing'],'路线仍可有效，但不能直接参加人口加权指标'),('关键字段缺失/无效记录数',c['missing_key'],'编号、请求时间、距离、耗时或时窗不满足时列入排除明细'),('双向配对数',c['paired'] if c.get('bidirectional_applicable') else '不适用','同一逻辑客源—重点村—计划时点同时具备去程和返程的配对数；新版单向计划不适用'),('是否具备完整双向配对','是' if c.get('bidirectional_applicable') and c['paired']>0 else ('否' if c.get('bidirectional_applicable') else '不适用'),'仅当同一逻辑OD和同一计划时点同时有去程与返程才算配对'),
        ('正式分析纳入状态',formal_summary,'路线有效、正式研究纳入和指标可计算条件是三件不同的事'),
        ('目标范围',f'{selected_targets}个本任务所选重点村','若为附近目标模式，不代表94个重点村全量'),('实际请求起止时间（北京时间）',f"{c['actual_start'] or ''} — {c['actual_end'] or ''}",'以实际请求/响应时间为准，计划时间不替代实际时间'),
        ('基础质量规则','真实高德观测；请求成功；距离和耗时可解析且有效；逻辑ID和方向稳定；有实际请求时间；不使用历史缓存；无意外重复；定时请求在允许窗口内启动。','未知分段路况不等于畅通，也不自动否定有效耗时；路况覆盖率不等于旅行时间准确率'),
        ('历史手工锁定点数量',c['manual_lock'],'继续保留“历史手工锁定且已核验”来源标记，不改写成重新调用POI接口的结果'),
        ('历史快照说明','导出使用任务保存的客源地和重点村快照，不用今天CSV静默覆盖旧任务。','快照复核字段与当前CSV不同属于历史快照差异，不自动否定路线观测，也不重新触发坐标核查'),
        ('当前结论',c['conclusion'],'不声称已完成新版20,398条正式采集，也不声称验证高德预测精度'),
    ]
    for row in records: ws.append([safe_cell(ws,x) for x in row])
    for (departure,direction), values in sorted(c['batches'].items()):
        planned,success,eligible=values
        rate=round(eligible/planned*100,2) if planned else None
        ws.append([safe_cell(ws,'分方向分批次有效完整率'),safe_cell(ws,f'{departure} · {DIRECTION_LABELS.get(direction,direction)}：{eligible}/{planned}（{rate}%）'),safe_cell(ws,'分母为本批次计划观测；有效为成功、非缓存且通过时间规则的路线')])
    ws.append([]); ws.append([safe_cell(ws,'排除记录'),safe_cell(ws,'请勿将排除记录解释为不可达'),safe_cell(ws,'')])
    exclusion_headers=[]
    for x in ['观测编号','客源地编号','重点村编号','方向','计划时点','排除原因']:
        cell=safe_cell(ws,x); cell.font=Font(color='FFFFFF',bold=True); cell.fill=PatternFill('solid',fgColor='6A1B9A'); exclusion_headers.append(cell)
    ws.append(exclusion_headers)
    for row in c['excluded']:
        ws.append([safe_cell(ws,row.get(k)) for k in ['observation_id','source_id','destination_id','direction','planned_datetime','reason']])
    # Keep the requested provenance and filtering fields in the quality
    # report, not in the analytical long table.  This detail block is limited
    # to rows that passed the base route rules; excluded rows are listed above
    # with their explicit reason and are never silently converted to zeros.
    ws.append([])
    ws.append([safe_cell(ws,'质量审核明细'),safe_cell(ws,'以下字段为审核/追溯信息，不作为论文数值变量'),safe_cell(ws,'可用观测可按观测编号与论文分析主表连接')])
    audit_headers=['记录状态']+[label for _,label in AUDIT_COLUMNS]
    audit_cells=[]
    for label in audit_headers:
        cell=safe_cell(ws,label); cell.font=Font(color='FFFFFF',bold=True); cell.fill=PatternFill('solid',fgColor='455A64'); audit_cells.append(cell)
    ws.append(audit_cells)
    for row in valid_rows:
        values=['主表纳入']
        for key,_label in AUDIT_COLUMNS:
            value=row.get(key)
            if key in {'verification_status','quality_result','formal_plan_eligibility'}:
                value=_label_value(key,value)
            values.append(value)
        ws.append([safe_cell(ws,value) for value in values])
    return ws

def _field_sheet(wb):
    ws=sheet(wb,'字段说明',['中文字段','内部字段','含义','单位/类型','来源','缺失值处理','所在工作表'])
    main_labels={label for _key,label in PAPER_COLUMNS}
    quality_labels={label for _key,label in AUDIT_COLUMNS}
    for row in PAPER_FIELD_DOCS:
        label=row[0]
        if label in quality_labels or label in {'方案版本/是否调整方案','人口加权指标资格','质量检查结果','正式分析纳入状态','正式计划资格','说明'}:
            location='数据质量说明（质量审核/汇总）'
        elif label=='计划采集日期/时间':
            location='论文分析主表（计划日期）；质量报告/计划快照（计划时点）'
        elif label=='客源地编号/名称/省份':
            location='论文分析主表（编号/名称）；客源地基础表（省份）'
        elif label in main_labels:
            location='论文分析主表'
        elif label=='人口年份/单位/口径/来源':
            location='论文分析主表（年份/口径）；数据质量说明（单位/来源）'
        elif label in {'地图服务/数据类型','相对计划时间偏移','是否跨窗口响应'}:
            location='数据质量说明（质量审核/汇总）'
        else:
            location='字段说明及相关工作表'
        ws.append([safe_cell(ws,x) for x in row]+[safe_cell(ws,location)])
    ws.append([safe_cell(ws,'通用限制'),safe_cell(ws,''),safe_cell(ws,'主表是后续计算指标的基础数据，不是已计算完成的论文结果；不未经确认自动计算延误率、可靠性、重力型客源市场潜力或潜力损失。'),safe_cell(ws,''),safe_cell(ws,''),safe_cell(ws,''),safe_cell(ws,'论文分析主表/数据质量说明')])
    return ws

def export_paper(db,path,jid):
    """Export the three-sheet Chinese workbook used by the paper workflow."""
    c=_paper_context(db,jid); wb=Workbook(write_only=True)
    ws=sheet(wb,'论文分析主表',[label for _,label in PAPER_COLUMNS])
    for row in c['valid']:
        ws.append([safe_cell(ws,row.get(key)) for key,_ in PAPER_COLUMNS])
    _quality_sheet(wb,c); _field_sheet(wb); wb.save(path)

def export_quality(db,path,jid):
    c=_paper_context(db,jid); wb=Workbook(write_only=True); _quality_sheet(wb,c); _field_sheet(wb); wb.save(path)

def export_package(db, path, jid, include_raw=True):
    """Write a reproducible research package without exposing credentials.

    CSV is used inside the ZIP so large jobs are not constrained by Excel's
    worksheet row limit.  The raw response index points to files stored under
    the application's raw_responses directory.  The caller explicitly
    chooses whether those files are copied into the package; the index is
    retained either way.
    """
    root=db.path.parent; mem=io.BytesIO()
    with zipfile.ZipFile(mem,'w',zipfile.ZIP_DEFLATED) as z:
        def write_csv(name,headers,rows):
            text=io.StringIO(newline=''); writer=csv.writer(text); writer.writerow(headers)
            # Normalize each column with its internal field name before
            # translating the displayed header.  ``raw_json`` and related
            # containers are never parsed or rewritten.
            normalized=[]
            for row in rows:
                values=[]
                for index,value in enumerate(row):
                    field=headers[index] if index<len(headers) else ''
                    if isinstance(value,(list,dict,tuple)) and field not in RAW_FIELDS:
                        value=json.dumps(value,ensure_ascii=False)
                    value=_clean(value) if field not in RAW_FIELDS else value
                    if field not in RAW_FIELDS: value=_label_value(field,value)
                    # CSV readers may hand formula-looking cells to Excel;
                    # protect those real values while leaving genuine blanks
                    # as empty fields (never a lone apostrophe).
                    if field not in RAW_FIELDS and isinstance(value,str) and value[:1] in '=+-@':
                        value="'"+value
                    values.append(value)
                normalized.append(values)
            display_headers=[_package_header(h) for h in headers]
            writer=csv.writer(text); text.seek(0); text.truncate(0); writer.writerow(display_headers); writer.writerows(normalized)
            payload=text.getvalue().encode('utf-8-sig')
            primary=PACKAGE_FILES.get(name,name)
            z.writestr(primary,payload)
            # Keep a byte-for-byte named compatibility alias for existing
            # scripts.  The Chinese file is the documented user-facing one;
            # the alias avoids breaking old audit tooling during migration.
            if primary!=name: z.writestr(name,payload)
        job=db.one('SELECT * FROM jobs WHERE id=?',(jid,))
        cfg=json.loads(job['config'])
        write_csv('schedule.csv',['planned_datetime','phase','day_type','window_minutes','directions','notes','template_version','schedule_adjusted','schedule_window_confirmed'],[(d,(cfg.get('departure_metadata') or {}).get(d,{}).get('phase',''),(cfg.get('departure_metadata') or {}).get(d,{}).get('day_type',cfg.get('day_type','custom')),(cfg.get('departure_metadata') or {}).get(d,{}).get('window_minutes',cfg.get('window_minutes')),json.dumps((cfg.get('departure_metadata') or {}).get(d,{}).get('directions',cfg.get('directions',['outbound'])),ensure_ascii=False), (cfg.get('departure_metadata') or {}).get(d,{}).get('notes',''),cfg.get('schedule_template',''),int(bool(cfg.get('schedule_adjusted',False))),int(bool(cfg.get('schedule_window_confirmed',False)))) for d in cfg.get('departures',[])])
        source_rows=cfg.get('source_snapshot') or db.rows('SELECT point_id,name,lon,lat,crs,source_id,source_name,province,city,county,population,population_year,population_scope,population_source,notes FROM points WHERE dataset_id=? AND valid=1 ORDER BY id',(job['dataset_id'],))
        source_headers=list(source_rows[0].keys()) if source_rows else ['point_id','name']
        # Keep the snapshot's historical columns and add the import-schema
        # aliases used by research workflows.  This avoids breaking old
        # consumers while making the exported meaning unambiguous.
        for alias in ('source_id','source_name','longitude','latitude','coordinate_system','wgs84_longitude','wgs84_latitude'):
            if alias not in source_headers: source_headers.append(alias)
        def source_value(row,key):
            aliases={'longitude':'lon','latitude':'lat','coordinate_system':'crs','wgs84_longitude':'wlon','wgs84_latitude':'wlat'}
            return row.get(aliases.get(key,key),row.get(key))
        write_csv('source_nodes.csv',source_headers,[[source_value(r,h) for h in source_headers] for r in source_rows])
        # Keep every original import column, including columns that the
        # routing schema does not understand.  ``raw_json`` is intentionally
        # retained as a lossless audit value; the readable source_nodes.csv
        # above is the normalized routing snapshot.
        input_datasets=db.rows('SELECT id,name,created,columns_json,crs,stats FROM datasets WHERE id=? ORDER BY created',(job['dataset_id'],))
        write_csv('import_datasets.csv',['dataset_id','file_name','imported_at','columns_json','file_crs','stats_json'],[[d.get('id'),d.get('name'),d.get('created'),d.get('columns_json'),d.get('crs'),d.get('stats')] for d in input_datasets])
        input_rows=db.rows('''SELECT p.dataset_id,d.name AS dataset_name,p.row_no,p.raw,p.point_id,p.name,p.lon,p.lat,p.crs,p.wlon,p.wlat,p.valid,p.issues
                              FROM points p JOIN datasets d ON d.id=p.dataset_id WHERE p.dataset_id=? ORDER BY p.dataset_id,p.row_no''',(job['dataset_id'],))
        write_csv('import_rows.csv',['dataset_id','dataset_name','row_no','raw_json','normalized_id','normalized_name','routing_lon','routing_lat','routing_crs','wgs84_lon','wgs84_lat','valid','issues'],[[r.get('dataset_id'),r.get('dataset_name'),r.get('row_no'),r.get('raw'),r.get('point_id'),r.get('name'),r.get('lon'),r.get('lat'),r.get('crs'),r.get('wlon'),r.get('wlat'),r.get('valid'),r.get('issues')] for r in input_rows])
        target_rows=cfg.get('targets',[])
        destination_headers=['id','target_id','destination_id','name','destination_name','lon','lat','longitude','latitude','crs','coordinate_system','province','city','county','village_name','entrance_name','coordinate_source','verification_status','quality_level','note','notes','source_dataset_id','source_row_no','raw_json']
        write_csv('destinations.csv',destination_headers,[[t.get('id',''),t.get('target_id',''),t.get('target_id',''),t.get('name',''),t.get('name',''),t.get('lon',''),t.get('lat',''),t.get('lon',''),t.get('lat',''),t.get('crs',''),t.get('crs',''),t.get('province',''),t.get('city',''),t.get('county',''),t.get('village_name',''),t.get('entrance_name',''),t.get('coordinate_source',''),t.get('verification_status',''),t.get('category',''),t.get('note',''),t.get('note',''),t.get('source_dataset_id',''),t.get('source_row_no'),t.get('raw','')] for t in target_rows])
        # Destination imports are kept separately from the source-node import
        # because a task can use a source CSV and a different destination CSV.
        # The raw JSON is the exact original row, including fields that are
        # not needed by the routing schema.
        destination_import_rows=[t for t in target_rows if t.get('source_dataset_id')]
        write_csv('destination_import_rows.csv',['target_internal_id','destination_id','dataset_id','row_no','raw_json'],[[t.get('id',''),t.get('target_id',''),t.get('source_dataset_id',''),t.get('source_row_no'),t.get('raw','')] for t in destination_import_rows])
        rows=db.rows('SELECT * FROM results WHERE job_id=? ORDER BY departure,query_order,id',(jid,))
        valid_rows,_excluded_rows=_classify_paper_rows(job,cfg,rows)
        valid_observation_ids={str(r.get('observation_id')) for r in valid_rows if r.get('observation_id') not in (None,'')}
        if rows:
            headers=list(rows[0].keys()); write_csv('observations.csv',headers,[[r.get(h) for h in headers] for r in rows])
        else: write_csv('observations.csv',['observation_id'],[])
        attempts=db.rows('SELECT * FROM request_attempts WHERE job_id=? ORDER BY id',(jid,))
        write_csv('request_attempts.csv',list(attempts[0].keys()) if attempts else ['attempt_id','observation_id'],[list(r.values()) for r in attempts])
        segments=db.rows('''SELECT s.* FROM traffic_segments s JOIN results r ON r.observation_id=s.observation_id WHERE r.job_id=? ORDER BY s.observation_id,s.segment_index''',(jid,))
        write_csv('traffic_segments.csv',list(segments[0].keys()) if segments else ['observation_id','segment_index','status_raw','status_normalized','length_m','polyline','raw_json'],[list(r.values()) for r in segments])
        # A compact quality table is included in every package so the CSV/ZIP
        # export can be audited without having to regenerate the in-app
        # report.  Counts are observation counts (not request-attempt counts),
        # and therefore sum to the planned rows for each time/direction slot.
        attempts_by_observation={}
        attempt_metrics_by_observation={}
        for a in attempts:
            key=a.get('observation_id')
            attempts_by_observation[key]=attempts_by_observation.get(key,0)+1
            metric=attempt_metrics_by_observation.setdefault(key,{'provider':[],'raw':[],'segment':[],'database':[],'persist':[],'total':[]})
            for name,field in [('provider','provider_duration_seconds'),('raw','raw_persistence_duration_seconds'),('segment','segment_persistence_duration_seconds'),('database','database_duration_seconds'),('persist','persistence_duration_seconds'),('total','total_duration_seconds')]:
                value=_number(a.get(field))
                if value is not None: metric[name].append(value)
        quality={}
        for r in rows:
            key=(r.get('departure') or '',r.get('direction') or '')
            q=quality.setdefault(key,{'planned':0,'success':0,'failed':0,'pending':0,
                                      'missed':0,'window_started':0,'crossed':0,
                                      'traffic':0,'traffic_coverage_sum':0.0,'eligible':0,
                                      'attempts':0,'provider':[],'raw':[],'segment':[],'database':[],'persist':[],'total':[]})
            q['planned']+=1
            status=r.get('status') or 'pending'
            if status=='success': q['success']+=1
            elif status=='failed': q['failed']+=1
            else: q['pending']+=1
            if str(r.get('observation_id')) in valid_observation_ids: q['eligible']+=1
            if r.get('window_miss_reason'): q['missed']+=1
            if r.get('requested_at') or r.get('request_started_at'): q['window_started']+=1
            if r.get('response_crossed_window'): q['crossed']+=1
            if r.get('traffic_coverage') is not None:
                q['traffic']+=1
                try: q['traffic_coverage_sum']+=float(r.get('traffic_coverage') or 0)
                except (TypeError,ValueError): pass
            q['attempts']+=attempts_by_observation.get(r.get('observation_id'),0)
            metric=attempt_metrics_by_observation.get(r.get('observation_id'),{})
            for name in ('provider','raw','segment','database','persist','total'):
                q[name].extend(metric.get(name,[]))
        quality_rows=[]
        for (planned_datetime,direction),q in sorted(quality.items()):
            planned=q['planned']
            meta=(cfg.get('departure_metadata') or {}).get(planned_datetime,{})
            avg=lambda name: round(sum(q[name])/len(q[name]),6) if q[name] else None
            quality_rows.append([planned_datetime,meta.get('phase',''),meta.get('day_type',cfg.get('day_type','custom')),direction,planned,q['success'],q['failed'],q['pending'],q['missed'],q['window_started'],q['crossed'],q['attempts'],q['traffic'],round(q['traffic_coverage_sum']/q['traffic'],4) if q['traffic'] else None, q['eligible'],round(q['eligible']/planned*100,4) if planned else None, cfg.get('data_use','unmarked'),(cfg.get('execution_limits') or {}).get('qps',(cfg.get('capacity_estimate') or {}).get('configured_qps')),(cfg.get('execution_limits') or {}).get('max_concurrency',(cfg.get('capacity_estimate') or {}).get('max_concurrency')),avg('provider'),avg('raw'),avg('segment'),avg('database'),avg('persist'),avg('total')])
        write_csv('quality_report.csv',['planned_datetime','phase','date_group','direction','planned_observations','successful_observations','failed_observations','pending_observations','missed_window_observations','window_started_observations','responses_crossed_window','request_attempts','traffic_observations','mean_traffic_coverage_percent','paper_valid_observations','complete_rate_percent','data_use','configured_qps','max_concurrency','avg_provider_duration_seconds','avg_raw_persistence_seconds','avg_segment_persistence_seconds','avg_database_duration_seconds','avg_persistence_duration_seconds','avg_total_duration_seconds'],quality_rows)
        raw_index=[]
        raw_dir=root/'raw_responses'
        for r in rows:
            rel=r.get('raw_response_path') or ''
            file=raw_dir/rel if rel and Path(rel).name==rel else None
            exists=bool(file and file.is_file())
            raw_index.append([r.get('observation_id'),('原始响应/'+rel if rel else None),int(exists),file.stat().st_size if exists else None,'ok' if exists else ('未生成' if not rel else '文件缺失或路径无效')])
        # Failed/retried attempts can have their own raw response even when
        # the observation has no successful route file.  Keep them in the
        # same index so an audit can account for every persisted response.
        known={(row[0],str(row[1] or '').removeprefix('原始响应/')) for row in raw_index}
        for a in attempts:
            rel=a.get('raw_response_path') or ''
            if not rel or (a.get('observation_id'),rel) in known: continue
            file=raw_dir/rel if Path(rel).name==rel else None
            exists=bool(file and file.is_file())
            raw_index.append([a.get('observation_id'),('原始响应/'+rel if rel else None),int(exists),file.stat().st_size if exists else None,'ok' if exists else '文件缺失或路径无效'])
        write_csv('raw_response_index.csv',['observation_id','raw_response_path','file_exists','file_size_bytes','integrity_status'],raw_index)
        field_desc={
            'source_id':'客源节点稳定 ID；同一任务内不因方向改变',
            'destination_id':'旅游目的地稳定 ID；同一任务内不因方向改变',
            'direction':'outbound=客源节点到目的地；inbound=目的地到客源节点',
            'departure':'计划发起时间（北京时间，非历史路况时间）',
            'requested_at':'实际请求开始时间（UTC）',
            'fetched_at':'收到接口响应时间（UTC）',
            'distance_meters':'地图服务道路距离（米）',
            'duration_seconds':'地图服务预计耗时（秒）',
            'traffic_coverage':'有明确分段长度的路况占路线长度百分比；未知不计为畅通',
            'raw_response_path':'脱敏原始响应文件相对路径；Key 不写入',
            'window_miss_reason':'窗口结束时未采集或未完成的原因分类；不等于路线不可达',
            'status':'success/failed/pending；失败或未采集不等于不可达',
            'cache_hit':'研究任务应为 0；仅用于识别历史缓存',
            'provider_error_code':'地图服务返回的错误码或本地归类码；网络错误等无业务码时使用归类值'
            ,'rate_wait_seconds':'请求启动前的平滑限流等待秒数（单调时钟）'
            ,'provider_duration_seconds':'接口调用及响应解析耗时（单调时钟）'
            ,'raw_persistence_duration_seconds':'原始响应 JSON 序列化、文件写入和 fsync 耗时'
            ,'segment_persistence_duration_seconds':'分段路况写入耗时'
            ,'database_duration_seconds':'结果/计数事务提交耗时'
            ,'persistence_duration_seconds':'原始响应与数据库持久化总耗时'
            ,'total_duration_seconds':'单条观测从工作线程开始到完成的单调时钟耗时'
        }
        write_csv('data_dictionary.csv',['field','description'],[(h,field_desc.get(h,'路线观测字段；单位见字段名或原始响应')) for h in (list(rows[0].keys()) if rows else ['observation_id'])])
        readme=('研究数据包\n本包的中文文件是面向用户的正式入口；同名英文文件为兼容旧脚本保留的别名。\n软件版本：'+APP_VERSION+'。时间字段带 UTC 或北京时间标记；模拟数据不用于真实交通研究。\n主表仅保留通过路线质量规则的观测；缺失或失败不等于不可达。任务使用创建时快照，不用今天CSV覆盖历史元数据。\n'+json.dumps({'job_id':jid,'provider':PROVIDER_LABELS.get(cfg.get('provider'),cfg.get('provider')),'data_use':DATA_USE_LABELS.get(cfg.get('data_use','unmarked'),'未标注'),'strategy':cfg.get('strategy'),'directions':[DIRECTION_LABELS.get(x,x) for x in cfg.get('directions',['outbound'])],'random_seed':cfg.get('random_seed')},ensure_ascii=False,indent=2)).encode('utf-8')
        z.writestr(PACKAGE_FILES['README.txt'],readme); z.writestr('README.txt',readme)
        raw_dir=root/'raw_responses'
        if include_raw:
            written_raw=set()
            for r in rows:
                rel=r.get('raw_response_path')
                if rel and Path(rel).name==rel and (raw_dir/rel).is_file():
                    z.write(raw_dir/rel,'原始响应/'+rel); z.write(raw_dir/rel,'raw_responses/'+rel)
                if rel: written_raw.add(rel)
            for a in attempts:
                rel=a.get('raw_response_path')
                if rel and rel not in written_raw and Path(rel).name==rel and (raw_dir/rel).is_file():
                    z.write(raw_dir/rel,'原始响应/'+rel); z.write(raw_dir/rel,'raw_responses/'+rel); written_raw.add(rel)
    path.write_bytes(mem.getvalue())
