from providers.base import RouteProvider,RouteError

class BaiduProvider(RouteProvider):
    """Reserved adapter, deliberately unavailable in the UI until integrated and tested."""
    name='baidu'; crs='BD-09'
    def route(self,origin,destination,departure,strategy):
        raise RouteError('百度 Provider 尚未接入；请选择高德或 Mock')
