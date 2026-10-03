from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict

class RouteError(Exception):
    def __init__(self,message,retryable=False,fatal=False,raw_response=None,http_status=None,business_status='',provider_error_code=''):
        super().__init__(message)
        self.retryable=retryable
        self.fatal=fatal
        # Keep the provider response available to the worker so failed
        # attempts can be audited just like successful attempts.  This is
        # deliberately separate from the human-readable error message.
        self.raw_response=raw_response
        self.http_status=http_status
        self.business_status=business_status or ''
        # Provider-specific code (for example AMap's ``infocode``).  Keep it
        # separate from the human-readable message so exports can distinguish
        # a business rejection from a local/network exception.
        self.provider_error_code=str(provider_error_code or '')

@dataclass
class RouteResult:
    distance_meters: float
    duration_seconds: float
    traffic_type: str
    provider: str
    raw_status: str
    # Optional provenance fields are populated by providers that expose them;
    # defaults keep the public dataclass backwards compatible with old tests and
    # third-party provider adapters.
    raw_response: dict|None = None
    route_index: int = 0
    route_id: str = ''
    api_version: str = ''
    traffic_segments: list|None = None
    http_status: int|None = None
    business_status: str = ''
    provider_error_code: str = ''
    def dict(self): return asdict(self)

class RouteProvider(ABC):
    name=''; crs='WGS84'; supports_departure=False; ttl=300
    @abstractmethod
    def route(self,origin,destination,departure,strategy,include_traffic=False): ...
