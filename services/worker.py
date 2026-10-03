import hashlib
import json
import logging
import os
from pathlib import Path
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
import httpx
from providers.amap import AmapProvider
from providers.mock import MockProvider
from providers.base import RouteError
from utils.geo import haversine,convert

log=logging.getLogger('traffic')
def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def cache_key(origin,dest,crs,departure,strategy,provider):
    payload=[list(origin),list(dest),crs,departure,strategy,provider,'route-v1']
    return hashlib.sha256(json.dumps(payload,separators=(',',':')).encode()).hexdigest()

def order_key(cfg,departure,point,target,direction='outbound'):
    if cfg.get('query_order')!='randomized': return ''
    # Preserve the legacy outbound key for old jobs while ensuring that the
    # two directions of a research pair are independently interleaved.
    suffix='' if direction=='outbound' else f'|{direction}'
    payload=f"{cfg['random_seed']}|{departure}|{point}|{target}{suffix}"
    return hashlib.sha256(payload.encode()).hexdigest()

def observation_id(job_id, point, target, departure, direction):
    return hashlib.sha256(f'{job_id}|{point}|{target}|{departure}|{direction}'.encode()).hexdigest()[:32]

def normalized_traffic_status(value):
    value=str(value or '').strip().lower()
    if value in {'畅通','smooth','good','free'}: return '畅通'
    if value in {'缓行','slow','moderate'}: return '缓行'
    if value in {'拥堵','congested','busy'}: return '拥堵'
    if value in {'严重拥堵','very congested','severe'}: return '严重拥堵'
    return '未知'


class SmoothRateLimiter:
    """A process-wide, smooth request-start limiter.

    ``qps`` is deliberately separate from worker concurrency.  The limiter
    reserves one monotonically increasing start slot at a time, so several
    worker threads cannot each send their own burst of ``qps`` requests.
    ``time.monotonic_ns`` is used for interval accounting; it is never used as
    the research request timestamp saved to the database.
    """

    def __init__(self, qps=3.0):
        self._lock = threading.Lock()
        # Add a small guard band so scheduler wake-up jitter cannot put four
        # starts in a closed/rounding-sensitive sliding one-second window. It
        # is a ceiling (not a promise to consume the full account QPS) and is
        # intentionally omitted from the optimistic capacity lower bound.
        self._interval = 1.0 / max(float(qps), 0.001) + 0.01
        self._next_slot = time.monotonic()

    def set_qps(self, qps):
        interval = 1.0 / max(float(qps), 0.001) + 0.01
        with self._lock:
            self._interval = interval
            # If the setting is lowered while a task is running, do not move
            # an already-reserved slot backwards and create a burst.
            self._next_slot = max(self._next_slot, time.monotonic())

    def acquire(self, stop_event, deadline=None):
        """Reserve a start slot and wait for it.

        Returns ``(granted, waited_seconds)``.  A deadline is checked both
        before and after waiting so a scheduled task never starts a new HTTP
        request after its allowed window.
        """
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            if deadline is not None and time.time() + max(0.0, slot - now) >= deadline:
                return False, 0.0
            self._next_slot = slot + self._interval
        wait = max(0.0, slot - now)
        # Event.wait may wake a few milliseconds early on some platforms.
        # Re-check the monotonic deadline so the actual provider start cannot
        # move ahead of the reserved slot and violate the sliding-window cap.
        while True:
            remaining = slot - time.monotonic()
            if remaining <= 0:
                break
            if stop_event.wait(remaining):
                return False, wait
        if deadline is not None and time.time() >= deadline:
            return False, wait
        return True, wait


class DispatchGate:
    """Preserve the saved query order while allowing responses to overlap."""

    def __init__(self, size):
        self._condition=threading.Condition()
        self._next=0
        self._size=size

    def wait_turn(self, index, stop_event):
        with self._condition:
            while self._next != index:
                if stop_event.is_set(): return False
                self._condition.wait(.05)
            return True

    def advance(self):
        with self._condition:
            self._next+=1
            self._condition.notify_all()

class Worker:
    def __init__(self,db,settings):
        self.db=db; self.settings=settings; self.stop_event=threading.Event(); self.last_call=0
        self.raw_dir=db.path.parent/'raw_responses'; self.raw_dir.mkdir(parents=True,exist_ok=True)
        self.thread=None
        self.rate_limiter=SmoothRateLimiter(3.0)
        self._active_key=''
    def event(self,jid,message,level='info'):
        self.db.execute('INSERT INTO events(created,job_id,level,message) VALUES(?,?,?,?)',(now(),jid,level,message))
        log.log(logging.ERROR if level=='error' else logging.INFO,'%s %s',jid,message)
    def start(self):
        self.db.execute("UPDATE jobs SET status='paused',message='上次运行未正常结束；可继续断点' WHERE status IN ('running','queued')")
        self.thread=threading.Thread(target=self.loop,name='route-worker',daemon=False); self.thread.start()
    def close(self):
        self.stop_event.set()
        if self.thread: self.thread.join(timeout=25)
    def active(self,jid):
        job=self.db.one('SELECT status FROM jobs WHERE id=?',(jid,))
        return not self.stop_event.is_set() and job and job['status']=='running'
    def write_raw_response(self, filename, payload):
        """Atomically persist a provider response and return its relative path.

        The request key is never sent to this function, but redact it as a
        final defence in case a provider echoes credentials in a response or
        error body.  A temporary file is fsynced before it becomes visible to
        the database/export index.
        """
        safe_name=Path(str(filename)).name
        relative=safe_name
        temp=self.raw_dir/(safe_name+'.tmp')
        final=self.raw_dir/safe_name
        serialized=json.dumps(payload,ensure_ascii=False,separators=(',',':'),default=str)
        # The key is read once when a real task starts.  Avoid a keychain call
        # for every concurrent response; never include the key in a filename,
        # request-attempt record, or exported value.
        secret=self._active_key or self.settings.key()
        if secret: serialized=serialized.replace(secret,'[REDACTED]')
        with temp.open('w',encoding='utf-8') as raw_file:
            raw_file.write(serialized)
            raw_file.flush(); os.fsync(raw_file.fileno())
        os.replace(temp,final)
        return relative
    def ready_job(self):
        # Waiting schedules never occupy the worker before their next window.
        # Among runnable work, choose the earliest due observation so an old
        # queued task cannot hold up a scheduled batch whose window is now due.
        candidates=self.db.rows("SELECT * FROM jobs WHERE status IN ('queued','scheduled') ORDER BY created")
        runnable=[]
        for job in candidates:
            cfg=json.loads(job['config'])
            if cfg.get('collection_mode')!='scheduled':
                runnable.append((time.time(),job)); continue
            pending=self.db.one("SELECT min(departure) departure FROM results WHERE job_id=? AND status='pending'",(job['id'],))['departure']
            due=pending or (cfg['departures'][0] if not job['prepared'] else None)
            if due is not None:
                due_ts=datetime.fromisoformat(due).timestamp()
                if due_ts<=time.time(): runnable.append((due_ts,job))
        if not runnable: return None
        return min(runnable,key=lambda item:(item[0],item[1]['created']))[1]
    def expired(self,r,cfg):
        if cfg.get('collection_mode')!='scheduled': return False
        meta=(cfg.get('departure_metadata') or {}).get(r['departure'],{})
        minutes=meta.get('window_minutes',cfg.get('window_minutes',20))
        return time.time()>=datetime.fromisoformat(r['departure']).timestamp()+int(minutes)*60
    def loop(self):
        while not self.stop_event.is_set():
            job=self.ready_job()
            if not job:
                self.stop_event.wait(.3); continue
            jid=job['id']
            self.db.execute("UPDATE jobs SET status='running',message='' WHERE id=? AND status IN ('queued','scheduled')",(jid,))
            try: self.run(job)
            except Exception:
                log.exception('Worker internal error')
                self.db.execute("UPDATE jobs SET status='failed',message='内部错误；查看日志后可继续' WHERE id=?",(jid,))
                self.event(jid,'任务内部错误','error')
        self.db.execute("UPDATE jobs SET status='paused',message='应用已退出，可继续' WHERE status IN ('running','queued')")
    def prepare(self,job,cfg):
        cursor=job['prepared']
        while self.active(job['id']):
            if cfg.get('source_snapshot') is not None:
                points=[p for p in cfg['source_snapshot'] if p.get('id',0)>cursor][:200]
            else:
                points=self.db.rows('SELECT * FROM points WHERE dataset_id=? AND valid=1 AND id>? AND (?="" OR category=?) ORDER BY id LIMIT 200',(job['dataset_id'],cursor,cfg.get('origin_category',''),cfg.get('origin_category','')))
            if not points: break
            with self.db.connect() as db:
                for p in points:
                    targets=[(t,haversine((p['wlon'],p['wlat']),(t['wlon'],t['wlat']))) for t in cfg['targets']]
                    targets.sort(key=lambda x:x[1])
                    if cfg['mode']=='saving': targets=targets[:cfg['candidates']]
                    for t,straight in targets:
                        for departure in cfg['departures']:
                            # A research calendar may override direction per
                            # date/time; legacy jobs keep task-level directions.
                            directions=(cfg.get('departure_metadata') or {}).get(departure,{}).get('directions') or cfg.get('directions') or ['outbound']
                            for direction in directions:
                                oid=observation_id(job['id'],p['id'],t['id'],departure,direction)
                                batch_id=hashlib.sha256(departure.encode()).hexdigest()[:16]
                                meta=(cfg.get('departure_metadata') or {}).get(departure,{})
                                window_end=(datetime.fromisoformat(departure)+timedelta(minutes=int(meta.get('window_minutes',cfg.get('window_minutes',20))))).isoformat() if cfg.get('collection_mode')=='scheduled' else None
                                # Keep the logical source/destination IDs stable while also recording
                                # the actual request orientation.  This lets a bidirectional study
                                # join outbound and inbound observations without guessing from names.
                                logical_source_id=str(p.get('source_id') or p.get('point_id') or p['id'])
                                logical_target_id=str(t.get('target_id') or t.get('id'))
                                origin_id=logical_source_id if direction=='outbound' else logical_target_id
                                route_target_id=logical_target_id if direction=='outbound' else logical_source_id
                                db.execute('''INSERT OR IGNORE INTO results(job_id,point_pk,target_id,departure,point_id,point_name,source_id,destination_id,origin_id,route_target_id,origin_lon,origin_lat,origin_crs,wlon,wlat,target_name,target_lon,target_lat,target_crs,twlon,twlat,straight_km,provider,query_order,direction,study_id,batch_id,observation_id,planned_datetime,window_end_datetime,route_selection_rule,route_index,api_version)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(job['id'],p['id'],t['id'],departure,p['point_id'],p['name'],logical_source_id,logical_target_id,origin_id,route_target_id,p['lon'],p['lat'],p['crs'],p['wlon'],p['wlat'],t['name'],t['lon'],t['lat'],t['crs'],t['wlon'],t['wlat'],straight,cfg['provider'],order_key(cfg,departure,p['id'],t['id'],direction),direction,cfg.get('study_id',''),batch_id,oid,departure,window_end,cfg.get('route_selection_rule','first'),0,cfg.get('api_version','')))
                    cursor=p['id']
                db.execute('UPDATE jobs SET prepared=? WHERE id=?',(cursor,job['id']))
    def _window_deadline(self, row, cfg):
        if cfg.get('collection_mode') != 'scheduled':
            return None
        value = row.get('window_end_datetime')
        if value:
            try:
                return datetime.fromisoformat(value).timestamp()
            except (TypeError, ValueError):
                return None
        meta=(cfg.get('departure_metadata') or {}).get(row.get('departure'),{})
        try:
            return datetime.fromisoformat(row['departure']).timestamp()+int(meta.get('window_minutes',cfg.get('window_minutes',20)))*60
        except (TypeError, ValueError, KeyError):
            return None

    def _mark_expired_pending(self, jid, departure):
        """Convert only not-started rows in one expired batch to window misses."""
        with self.db.connect() as db:
            pending=db.execute('''SELECT r.id,COUNT(a.id) attempts,
                MAX(CASE WHEN a.error IS NOT NULL THEN a.error ELSE '' END) last_error
                FROM results r LEFT JOIN request_attempts a
                  ON a.observation_id=r.observation_id AND a.job_id=r.job_id
                WHERE r.job_id=? AND r.departure=? AND r.status='pending'
                  AND coalesce(r.inflight,0)=0 GROUP BY r.id''',(jid,departure)).fetchall()
            count=0
            stamp=now()
            for item in pending:
                if item['attempts']:
                    reason='已发起请求但网络错误或重试未能在窗口内完成' if item['last_error'] else '已发起请求但响应未能在窗口内完成'
                else:
                    reason='未在窗口内启动，可能因排队、暂停、应用未运行或电脑休眠'
                db.execute("UPDATE results SET status='failed',error=?,window_miss_reason=?,calculated_at=?,saved_at=? WHERE id=? AND status='pending' AND coalesce(inflight,0)=0",(f'错过采集窗口：{reason}',reason,stamp,stamp,item['id']))
                count+=1
            if count:
                db.execute('UPDATE jobs SET failed=failed+? WHERE id=?',(count,jid))
        return count

    def _reserve_call(self, provider, daily_limit):
        """Atomically reserve one provider attempt from the local daily budget."""
        day=datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        with self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO usage(day,provider) VALUES(?,?)',(day,provider))
            calls=db.execute('SELECT calls FROM usage WHERE day=? AND provider=?',(day,provider)).fetchone()[0]
            if calls >= int(daily_limit):
                return False, day
            db.execute('UPDATE usage SET calls=calls+1 WHERE day=? AND provider=?',(day,provider))
        return True, day

    def _claim_rows(self, jid, rows):
        """Claim rows before submitting futures; recovery clears stale claims."""
        claimed=[]
        stamp=now()
        with self.db.connect() as db:
            for row in rows:
                cur=db.execute("UPDATE results SET inflight=1,inflight_started_at=? WHERE id=? AND status='pending' AND coalesce(inflight,0)=0",(stamp,row['id']))
                if cur.rowcount:
                    row=dict(row); row['inflight']=1; row['inflight_started_at']=stamp
                    claimed.append(row)
        return claimed

    def _clear_claim(self, row):
        self.db.execute("UPDATE results SET inflight=0,inflight_started_at=NULL WHERE id=?",(row['id'],))

    def _process_row(self, jid, cfg, provider, row, max_concurrency, dispatch_gate=None, dispatch_index=0):
        """Fetch and durably persist one observation in a bounded worker."""
        row_started_mono=time.monotonic_ns()
        r=row
        last_attempt_id=None
        requested_at=None
        result=None; error=None; provider_error_code=''; hit=False; fatal=False
        raw_persist_elapsed=None; segment_persist_elapsed=None; database_elapsed=None
        try:
            origin=convert(r['origin_lon'],r['origin_lat'],r['origin_crs'],provider.crs)
            dest=convert(r['target_lon'],r['target_lat'],r['target_crs'],provider.crs)
            if r.get('direction','outbound')=='inbound': origin,dest=dest,origin
            if provider.name=='amap':
                origin=tuple(round(v,6) for v in origin); dest=tuple(round(v,6) for v in dest)
            request_coords=json.dumps({'origin':origin,'destination':dest,'crs':provider.crs,
                'conversion_method':f'utils.geo.convert:{r["origin_crs"]}/{r["target_crs"]}->'+provider.crs,
                'direction':r.get('direction','outbound')},ensure_ascii=False,separators=(',',':'))
            key=cache_key(origin,dest,provider.crs,r['departure'],cfg['strategy'],provider.name)
            cached=None if cfg.get('fresh_collection') or cfg.get('collection_mode')=='scheduled' else self.db.one('SELECT value FROM cache WHERE key=? AND expires>?',(key,time.time()))
            hit=bool(cached)
            if cached:
                result=json.loads(cached['value'])
            else:
                settings=self.settings.get()
                self.rate_limiter.set_qps(settings.get('qps',3.0))
                for attempt in range(int(settings.get('retries',3))+1):
                    ordered_first=provider.name!='mock' and dispatch_gate is not None and attempt==0
                    if ordered_first:
                        if not dispatch_gate.wait_turn(dispatch_index,self.stop_event): break
                        if not self.active(jid):
                            dispatch_gate.advance()
                            break
                    elif not self.active(jid):
                        break
                    wait_seconds=0.0; day=None
                    if provider.name!='mock':
                        deadline=self._window_deadline(r,cfg)
                        try:
                            allowed,wait_seconds=self.rate_limiter.acquire(self.stop_event,deadline)
                        finally:
                            if ordered_first: dispatch_gate.advance()
                        if not allowed:
                            error='错过采集窗口：未在窗口内启动或应用已暂停'; break
                        reserved,day=self._reserve_call(provider.name,settings.get('daily_limit',1000))
                        if not reserved:
                            self.db.execute("UPDATE jobs SET status='paused',message='本机每日调用上限已到；调整预算或次日继续' WHERE id=?",(jid,))
                            return
                        if self.expired(r,cfg):
                            error='错过采集窗口：未继续请求'; break
                    if not self.active(jid): break
                    attempt_id=uuid.uuid4().hex; last_attempt_id=attempt_id
                    attempt_started=now(); provider_started_mono=time.monotonic_ns()
                    request_params=json.dumps({'origin':origin,'destination':dest,'crs':provider.crs,'strategy':cfg['strategy'],'direction':r.get('direction','outbound')},ensure_ascii=False,separators=(',',':'))
                    self.db.execute('INSERT INTO request_attempts(attempt_id,observation_id,job_id,started_at,request_params,rate_wait_seconds,max_concurrency) VALUES(?,?,?,?,?,?,?)',(attempt_id,r['observation_id'] or observation_id(jid,r['point_pk'],r['target_id'],r['departure'],r.get('direction','outbound')),jid,attempt_started,request_params,wait_seconds,int(max_concurrency)))
                    try:
                        stamp=attempt_started; requested_at=requested_at or r.get('requested_at') or stamp
                        self.db.execute('UPDATE results SET requested_at=coalesce(requested_at,?),request_started_at=coalesce(request_started_at,?),last_requested_at=?,attempts=coalesce(attempts,0)+1 WHERE id=?',(stamp,stamp,stamp,r['id']))
                        result=provider.route(origin,dest,r['departure'],cfg['strategy'],True).dict()
                        result['fetched_at']=now()
                        provider_elapsed=(time.monotonic_ns()-provider_started_mono)/1_000_000_000
                        self.db.execute('UPDATE request_attempts SET finished_at=?,http_status=?,business_status=?,provider_error_code=?,success=1,provider_duration_seconds=? WHERE attempt_id=?',(result['fetched_at'],result.get('http_status'),result.get('business_status') or result.get('raw_status'),result.get('provider_error_code',''),provider_elapsed,attempt_id))
                        break
                    except RouteError as exc:
                        provider_elapsed=(time.monotonic_ns()-provider_started_mono)/1_000_000_000
                        error=str(exc); provider_error_code=exc.provider_error_code
                        failed_raw_path=''
                        if exc.raw_response is not None:
                            raw_started_mono=time.monotonic_ns()
                            try:
                                failed_raw_path=self.write_raw_response(f'{r["observation_id"] or observation_id(jid,r["point_pk"],r["target_id"],r["departure"],r.get("direction","outbound"))}-attempt-{attempt_id}.json',exc.raw_response)
                                raw_persist_elapsed=(time.monotonic_ns()-raw_started_mono)/1_000_000_000
                            except OSError as raw_exc:
                                raw_persist_elapsed=(time.monotonic_ns()-raw_started_mono)/1_000_000_000
                                error=f'{error}；原始错误响应写入失败：{raw_exc}'
                                self.event(jid,'原始错误响应无法保存，请检查数据目录权限','error')
                        self.db.execute('UPDATE request_attempts SET finished_at=?,http_status=?,business_status=?,provider_error_code=?,error=?,retry_reason=?,raw_response_path=?,provider_duration_seconds=?,raw_persistence_duration_seconds=? WHERE attempt_id=?',(now(),exc.http_status,exc.business_status,exc.provider_error_code,error,'retryable' if exc.retryable else 'not_retryable',failed_raw_path,provider_elapsed,raw_persist_elapsed,attempt_id))
                        if provider.name!='mock' and day:
                            self.db.execute('UPDATE usage SET errors=errors+1 WHERE day=? AND provider=?',(day,provider.name))
                        self.event(jid,error,'error')
                        if exc.fatal:
                            self.db.execute("UPDATE jobs SET status='paused',message=? WHERE id=?",(error,jid)); fatal=True; break
                        if not exc.retryable or attempt>=int(settings.get('retries',3)): break
                        if self.stop_event.wait(min(2**attempt,16)): break
            if fatal:
                return
            if result is None and not self.active(jid):
                return
            # Commit the raw response before marking the observation successful.
            # Start the persistence timer before JSON serialization/fsync so
            # local storage work is visible separately from provider time.
            persist_started_mono=time.monotonic_ns()
            raw_path=''
            if result and result.get('raw_response') is not None:
                raw_path=f'{r["observation_id"] or observation_id(jid,r["point_pk"],r["target_id"],r["departure"],r.get("direction","outbound"))}.json'
                raw_started_mono=time.monotonic_ns()
                try:
                    self.write_raw_response(raw_path,result.get('raw_response'))
                    raw_persist_elapsed=(time.monotonic_ns()-raw_started_mono)/1_000_000_000
                except OSError as raw_exc:
                    raw_persist_elapsed=(time.monotonic_ns()-raw_started_mono)/1_000_000_000
                    error=f'原始响应写入失败：{raw_exc}'; result=None
                    self.event(jid,'原始响应无法保存，路线未计为成功；请检查数据目录权限','error')
                    if last_attempt_id:
                        self.db.execute('UPDATE request_attempts SET finished_at=?,success=0,error=?,retry_reason=?,raw_persistence_duration_seconds=? WHERE attempt_id=?',(now(),error,'raw_write_failed',raw_persist_elapsed,last_attempt_id))
                else:
                    if last_attempt_id:
                        self.db.execute('UPDATE request_attempts SET raw_response_path=? WHERE attempt_id=?',(raw_path,last_attempt_id))
            database_started_mono=time.monotonic_ns()
            with self.db.connect() as db:
                db.execute("UPDATE results SET requested_at=coalesce(requested_at,?) WHERE id=?",(requested_at,r['id']))
                if result:
                    if not hit and not cfg.get('fresh_collection') and cfg.get('collection_mode')!='scheduled': db.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?)',(key,json.dumps(result),time.time()+provider.ttl))
                    segments=result.get('traffic_segments') or []; lengths=[]
                    segment_started_mono=time.monotonic_ns()
                    for idx,seg in enumerate(segments):
                        try: length=float(seg.get('length_m')) if seg.get('length_m') is not None else None
                        except (TypeError,ValueError): length=None
                        if length is not None and length>=0: lengths.append(length)
                        segment_json=json.dumps(seg,ensure_ascii=False,separators=(',',':'))
                        secret=self._active_key
                        if secret: segment_json=segment_json.replace(secret,'[REDACTED]')
                        db.execute('INSERT INTO traffic_segments(observation_id,segment_index,status_raw,status_normalized,length_m,polyline,raw_json) VALUES(?,?,?,?,?,?,?)',(r['observation_id'],idx,str(seg.get('status_raw') or ''),normalized_traffic_status(seg.get('status_raw')),length,seg.get('polyline'),segment_json))
                    segment_persist_elapsed=(time.monotonic_ns()-segment_started_mono)/1_000_000_000
                    coverage=min(100.0,(sum(lengths)/float(result['distance_meters'])*100)) if lengths and result.get('distance_meters') else None
                    response_at=result.get('fetched_at') or now(); saved_at=now(); delay=None; crossed=0
                    try:
                        delay=datetime.fromisoformat(r.get('request_started_at') or requested_at).timestamp()-datetime.fromisoformat(r.get('planned_datetime') or r['departure']).timestamp()
                        crossed=int(bool(r.get('window_end_datetime')) and datetime.fromisoformat(response_at).timestamp()>=datetime.fromisoformat(r['window_end_datetime']).timestamp())
                    except (TypeError,ValueError,AttributeError): delay=None; crossed=0
                    db.execute("UPDATE results SET status='success',distance_meters=?,duration_seconds=?,traffic_type=?,raw_status=?,provider_error_code=?,request_coords=?,calculated_at=?,saved_at=?,cache_hit=?,fetched_at=?,response_received_at=?,error=NULL,raw_response_path=?,route_index=?,route_id=?,api_version=?,traffic_coverage=?,request_delay_seconds=?,response_crossed_window=? WHERE id=?",(result['distance_meters'],result['duration_seconds'],result['traffic_type'],result['raw_status'],result.get('provider_error_code',''),request_coords,saved_at,saved_at,int(hit),response_at,response_at,raw_path,result.get('route_index',0),result.get('route_id',''),result.get('api_version',''),coverage,delay,crossed,r['id']))
                    db.execute('UPDATE jobs SET success=success+1 WHERE id=?',(jid,))
                else:
                    miss_reason='已发起请求但重试或响应跨过窗口' if error and '错过采集窗口' in error else ''
                    db.execute("UPDATE results SET status='failed',error=?,provider_error_code=?,window_miss_reason=?,calculated_at=?,saved_at=?,request_coords=? WHERE id=?",(str(error or '未知路线错误'),provider_error_code,miss_reason,now(),now(),request_coords,r['id']))
                    db.execute('UPDATE jobs SET failed=failed+1 WHERE id=?',(jid,))
            database_elapsed=(time.monotonic_ns()-database_started_mono)/1_000_000_000
            if last_attempt_id:
                persist_elapsed=(time.monotonic_ns()-persist_started_mono)/1_000_000_000
                total_elapsed=(time.monotonic_ns()-row_started_mono)/1_000_000_000
                self.db.execute('UPDATE request_attempts SET raw_persistence_duration_seconds=?,segment_persistence_duration_seconds=?,database_duration_seconds=?,persistence_duration_seconds=?,total_duration_seconds=? WHERE attempt_id=?',(raw_persist_elapsed,segment_persist_elapsed,database_elapsed,persist_elapsed,total_elapsed,last_attempt_id))
        finally:
            self._clear_claim(r)

    def _run_rows(self, jid, cfg, provider, rows, max_concurrency):
        claimed=self._claim_rows(jid,rows)
        if not claimed: return
        if provider.name!='amap' or max_concurrency<=1:
            for row in claimed:
                if not self.active(jid): break
                self._process_row(jid,cfg,provider,row,1)
            return
        gate=DispatchGate(len(claimed))
        with ThreadPoolExecutor(max_workers=max_concurrency,thread_name_prefix='route-fetch') as pool:
            futures={pool.submit(self._process_row,jid,cfg,provider,row,max_concurrency,gate,index):row for index,row in enumerate(claimed)}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception:
                    row=futures[future]
                    self._clear_claim(row)
                    self.event(jid,'并发采集线程发生内部错误；任务已暂停，请查看日志后恢复','error')
                    logging.getLogger('traffic').exception('concurrent route worker failed for %s',jid)
                    self.db.execute("UPDATE jobs SET status='failed',message='并发采集内部错误；请查看日志后恢复' WHERE id=?",(jid,))

    def run(self,job):
        jid=job['id']; cfg=json.loads(job['config'])
        self.event(jid,'开始或继续任务')
        self.prepare(job,cfg)
        # A stale claim means the process exited after claiming a row but
        # before its durable result commit.  It is intentionally reset to
        # pending so the request may be attempted again and remains auditable.
        self.db.execute("UPDATE results SET inflight=0,inflight_started_at=NULL WHERE job_id=? AND status='pending'",(jid,))
        client=None
        if cfg['provider']=='mock':
            provider=MockProvider(); max_concurrency=1
        else:
            key=self.settings.key(); self._active_key=key
            client=httpx.Client(trust_env=False,timeout=15)
            provider=AmapProvider(key,client=client)
            max_concurrency=max(1,min(8,int(self.settings.get().get('max_concurrency',3))))
            self.rate_limiter.set_qps(self.settings.get().get('qps',3.0))
        try:
            while self.active(jid):
                batch_size=100 if provider.name!='amap' else max(4,max_concurrency*4)
                rows=self.db.rows("SELECT * FROM results WHERE job_id=? AND status='pending' AND coalesce(inflight,0)=0 ORDER BY departure,query_order,id LIMIT ?",(jid,batch_size))
                if not rows:
                    self.db.execute("UPDATE jobs SET status=CASE WHEN failed>0 THEN 'partial' ELSE 'completed' END WHERE id=? AND status='running'",(jid,))
                    self.event(jid,'任务处理完成'); break
                if cfg.get('collection_mode')=='scheduled':
                    departure=rows[0]['departure']
                    if datetime.fromisoformat(departure).timestamp()>time.time():
                        self.db.execute("UPDATE jobs SET status='scheduled',message='等待下一个采集时段' WHERE id=? AND status='running'",(jid,)); return
                    if self.expired(rows[0],cfg):
                        self._mark_expired_pending(jid,departure)
                        continue
                    # Never let a large query batch cross into the next
                    # scheduled departure.  The next natural time slot must
                    # wait for its own clock/window check.
                    rows=[row for row in rows if row['departure']==departure]
                self._run_rows(jid,cfg,provider,rows,max_concurrency)
        finally:
            if client is not None:
                client.close()
            self._active_key=''
