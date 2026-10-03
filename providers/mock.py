from providers.base import RouteProvider,RouteResult
from utils.geo import haversine

class MockProvider(RouteProvider):
    name='mock'; crs='WGS84'; ttl=31536000
    def route(self,origin,destination,departure,strategy,include_traffic=False):
        distance=haversine(origin,destination)*1000*1.25
        return RouteResult(distance,distance/1000/60*3600,'mock','mock','MOCK_SYNTHETIC_NOT_REAL_ROUTE',
                           raw_response={'synthetic':True,'notice':'not a real route'},api_version='mock-v1',business_status='synthetic')
