import httpx
import math
from providers.base import RouteProvider,RouteResult,RouteError

class AmapProvider(RouteProvider):
    name='amap'; crs='GCJ-02'; ttl=300
    def __init__(self,key,client=None):
        self.key=key
        self.client=client
    def route(self,origin,destination,departure,strategy,include_traffic=False):
        if not self.key: raise RouteError('未配置高德 Web 服务 Key',fatal=True)
        # ``tmcs`` is an optional response field in AMap Route Planning 2.0.
        # Keep the minimal ``cost`` request for backwards-compatible direct
        # provider use; research workers explicitly request segment traffic.
        params=dict(key=self.key,origin=f'{origin[0]:.6f},{origin[1]:.6f}',destination=f'{destination[0]:.6f},{destination[1]:.6f}',strategy=strategy,show_fields='cost,tmcs' if include_traffic else 'cost')
        try:
            if self.client:
                response=self.client.get('https://restapi.amap.com/v5/direction/driving',params=params,timeout=15)
            else:
                with httpx.Client(trust_env=False) as client:
                    response=client.get('https://restapi.amap.com/v5/direction/driving',params=params,timeout=15)
            if response.status_code==429 or response.status_code>=500:
                raise RouteError(f'地图 HTTP {response.status_code}',retryable=True,http_status=response.status_code,raw_response=response.text[:200000],provider_error_code=str(response.status_code))
            if response.status_code!=200: raise RouteError(f'地图 HTTP {response.status_code}',fatal=response.status_code in {401,403},http_status=response.status_code,raw_response=response.text[:200000],provider_error_code=str(response.status_code))
            data=response.json()
        except httpx.RequestError:
            raise RouteError('地图网络连接失败或超时',retryable=True,provider_error_code='NETWORK_ERROR') from None
        except ValueError:
            raise RouteError('地图返回非 JSON 数据',retryable=True,http_status=getattr(response,'status_code',None),raw_response=getattr(response,'text','')[:200000],provider_error_code='NON_JSON') from None
        if not isinstance(data,dict): raise RouteError('地图返回 JSON 结构无效',retryable=True,http_status=200,raw_response=data,provider_error_code='INVALID_JSON')
        if str(data.get('status'))!='1':
            code=str(data.get('infocode','UNKNOWN'))
            # Only whitelisted code is logged: no response URL / key / untrusted response text.
            raise RouteError(f'高德错误码 {code[:16]}',retryable=code in {'10014','10015','10016','10019','10020'},fatal=code in {'10001','10003','10004','10009','10010','10012','10013','10044'},http_status=200,business_status=str(data.get('status','')),provider_error_code=code,raw_response=data)
        try:
            path=data['route']['paths'][0]
            distance=float(path['distance']); duration=float(path['cost']['duration'])
            if not all(math.isfinite(v) and v>=0 for v in (distance,duration)): raise ValueError()
        except (KeyError,IndexError,TypeError,ValueError):
            raise RouteError('无可用驾车路线或返回距离/耗时字段缺失',http_status=200,business_status=str(data.get('status','')),provider_error_code=str(data.get('infocode','ROUTE_FIELDS_MISSING')),raw_response=data) from None
        segments=[]
        # The v5 response names these fields tmc_status/tmc_distance/
        # tmc_polyline.  Some gateway versions return one object and others
        # return a list, so accept both without inventing traffic values.
        def append_tmcs(value):
            if isinstance(value,dict): values=[value]
            elif isinstance(value,list): values=value
            else: values=[]
            for seg in values:
                if not isinstance(seg,dict): continue
                segments.append({'status_raw':seg.get('tmc_status',seg.get('status')),
                                 'length_m':seg.get('tmc_distance',seg.get('distance',seg.get('length'))),
                                 'polyline':seg.get('tmc_polyline',seg.get('polyline')),
                                 'raw':seg})
        append_tmcs(path.get('tmcs'))
        for step in path.get('steps',[]) or []:
            if isinstance(step,dict): append_tmcs(step.get('tmcs'))
        # API does not accept departure time. Never claim historical/future traffic.
        return RouteResult(distance,duration,'current_estimate','amap',str(data.get('infocode','10000')),
                           raw_response=data,route_index=0,route_id=str(path.get('id','')),
                           api_version='v5/direction/driving',traffic_segments=segments,
                           http_status=200,business_status=str(data.get('status','')),provider_error_code=str(data.get('infocode','10000')))
