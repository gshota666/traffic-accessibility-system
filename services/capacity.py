"""Planning estimates, not predictions of API latency or account quota."""
from datetime import datetime, timezone, timedelta


def _unique_directions(value, fallback=('outbound',)):
    values=list(dict.fromkeys(value or fallback))
    return values or list(fallback)


def _batch_specs(body):
    """Return one record for every planned departure.

    Directions may be overridden on an individual schedule item. Older
    clients omit that field and inherit the task-level direction list.
    """
    global_dirs=_unique_directions(getattr(body, 'directions', None))
    if body.collection_mode!='scheduled':
        return [{'date':datetime.now(timezone(timedelta(hours=8))).date().isoformat(),
                 'times': ['immediate'], 'directions': global_dirs,
                 'window_minutes': body.window_minutes}]
    schedule=getattr(body, 'schedule', None) or []
    specs=[]
    if schedule:
        for item in schedule:
            date=item.date.isoformat() if hasattr(item.date, 'isoformat') else str(item.date)
            dirs=_unique_directions(getattr(item, 'directions', None), global_dirs)
            for tm in sorted(set(item.times)):
                specs.append({'date':date,'times':[tm],'directions':dirs,
                              'window_minutes':item.window_minutes})
    else:
        dates=sorted(set(body.dates or ([body.date] if body.date else [])))
        for day in dates:
            date=day.isoformat() if hasattr(day, 'isoformat') else str(day)
            for tm in sorted(set(body.times)):
                specs.append({'date':date,'times':[tm],'directions':global_dirs,
                              'window_minutes':body.window_minutes})
    return specs


def estimate_capacity(db, settings, body):
    config=settings.get()
    source_ids=list(dict.fromkeys(getattr(body, 'source_point_ids', None) or []))
    if source_ids:
        placeholders=','.join('?' for _ in source_ids)
        count=db.one(f'SELECT count(*) n FROM points WHERE dataset_id=? AND valid=1 AND id IN ({placeholders})',[body.dataset_id,*source_ids])['n']
    else:
        count=db.one('SELECT count(*) n FROM points WHERE dataset_id=? AND valid=1', (body.dataset_id,))['n']
    ids=set(body.target_ids)
    targets=[t for t in db.rows('SELECT * FROM targets') if t['id'] in ids]
    destinations=min(body.candidates, len(targets)) if body.mode=='saving' else len(targets)
    specs=_batch_specs(body)
    base_routes=count*destinations
    batch_counts=[base_routes*len(spec['directions']) for spec in specs]
    routes=max(batch_counts, default=0)
    batches=len(batch_counts)
    total_planned_requests=sum(batch_counts)
    # QPS limits request starts; concurrency limits simultaneous provider
    # calls.  A capacity estimate must account for both rather than treating
    # ``routes / qps`` as a completion guarantee.  The estimate_seconds value
    # is a user-supplied/试采-derived end-to-end request estimate and is kept
    # separate from the pure rate-limit lower bound.
    qps=float(config.get('qps',3.0))
    concurrency=max(1,int(config.get('max_concurrency',3)))
    minimum=(routes/qps) if body.provider!='mock' else None
    single_worker=(routes*max(body.estimate_seconds,1/qps)) if body.provider!='mock' else None
    effective_rate=min(qps,concurrency/max(body.estimate_seconds,0.001)) if body.provider!='mock' else None
    planning=(routes/effective_rate) if effective_rate else None
    today=datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    usage=db.one('SELECT calls FROM usage WHERE day=? AND provider=?', (today,body.provider))
    remaining=max(0,config['daily_limit']-(usage['calls'] if usage else 0))
    daily_request_breakdown={}
    for spec,batch_count in zip(specs,batch_counts):
        daily_request_breakdown[spec['date']]=daily_request_breakdown.get(spec['date'],0)+batch_count
    max_daily_requests=max(daily_request_breakdown.values(), default=0)
    # A variable-direction calendar can have a different estimate for each
    # departure. Keep legacy scalar fields as the conservative maximum and
    # expose per-batch detail for the UI/audit export.
    batch_details=[dict(date=s['date'], directions=s['directions'], direction_count=len(s['directions']),
                        routes=batch_count, window_minutes=s['window_minutes'])
                   for s,batch_count in zip(specs,batch_counts)]
    windows_exceeded=[d for d in batch_details if body.provider!='mock' and body.collection_mode=='scheduled'
                      and d['routes'] and d['routes']/effective_rate>d['window_minutes']*60]
    warnings=[]
    if body.provider!='mock':
        if windows_exceeded:
            warnings.append('预计至少一个采集时段超过窗口，部分路线可能漏采；请减少组合、扩大窗口或调整研究设计。')
        if max_daily_requests>config['daily_limit']:
            warnings.append('每日计划请求数超过本软件每日上限，任务可能因额度暂停。')
        includes_today=body.collection_mode!='scheduled' or today in daily_request_breakdown
        today_plan=daily_request_breakdown.get(today,0)
        if includes_today and today_plan>remaining:
            warnings.append('计划请求数超过本软件今日剩余额度；该额度不等于高德账户实际剩余额度。')
        active=db.one("SELECT count(*) n FROM jobs WHERE status IN ('queued','running','scheduled')")['n']
        if active:
            warnings.append(f'还有 {active} 个待执行或运行任务，共用全局限流器、并发池与每日额度，可能延迟启动。')
    union_dirs=sorted({direction for spec in specs for direction in spec['directions']})
    # Keep both concepts visible: a natural time slot is one date+time, while
    # a direction time slice is one date+time+direction combination.  The
    # distinction matters for calendars that intentionally collect only one
    # direction on a holiday departure/return day.
    return dict(points=count, entrances=len(targets), directions=len(union_dirs),
                natural_time_slots=batches, direction_time_slices=sum(len(spec['directions']) for spec in specs),
                routes_per_batch=routes, batches=batches, total_routes=total_planned_requests,
                total_planned_requests=total_planned_requests, daily_requests=max_daily_requests,
                daily_request_breakdown=daily_request_breakdown, local_daily_limit=config['daily_limit'],
                # ``planning_minutes`` remains the public scalar consumed by
                # older clients, but now represents the bounded-concurrency
                # estimate.  The component estimates are explicit so reports
                # cannot confuse a lower bound with a completion prediction.
                minimum_minutes=minimum/60 if minimum is not None else None,
                single_worker_minutes=single_worker/60 if single_worker is not None else None,
                planning_minutes=planning/60 if planning is not None else None,
                estimated_minutes=planning/60 if planning is not None else None,
                effective_throughput_qps=effective_rate,
                configured_qps=qps if body.provider!='mock' else None,
                max_concurrency=concurrency if body.provider!='mock' else None,
                performance_evidence='离线模型（非真实验证）' if body.provider!='mock' else '不适用（模拟服务）',
                target_window_minutes=max((d['window_minutes'] for d in batch_details),default=None),
                assumed_seconds=body.estimate_seconds, window_minutes=body.window_minutes,
                window_exceeded=bool(windows_exceeded), batch_details=batch_details,
                today_remaining=remaining,
                max_attempts=total_planned_requests*(config['retries']+1) if body.provider!='mock' else 0,
                warnings=warnings)
