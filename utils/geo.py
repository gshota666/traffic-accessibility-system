"""Coordinate transforms are approximate, for accessibility analysis, not surveying."""
import math

SYSTEMS = ('WGS84', 'GCJ-02', 'BD-09')


def valid(lon, lat):
    return math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90


def haversine(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371.0088 * 2 * math.asin(math.sqrt(min(1, max(0, h))))


def wgs_gcj(lon, lat):
    if not (72.004 <= lon <= 137.8347 and 0.8293 <= lat <= 55.8271):
        return lon, lat
    x, y = lon-105, lat-35
    dlat = -100+2*x+3*y+0.2*y*y+0.1*x*y+0.2*math.sqrt(abs(x))
    dlon = 300+x+2*y+0.1*x*x+0.1*x*y+0.1*math.sqrt(abs(x))
    shared = (20*math.sin(6*x*math.pi)+20*math.sin(2*x*math.pi))*2/3
    dlat += shared+(20*math.sin(y*math.pi)+40*math.sin(y/3*math.pi))*2/3+(160*math.sin(y/12*math.pi)+320*math.sin(y*math.pi/30))*2/3
    dlon += shared+(20*math.sin(x*math.pi)+40*math.sin(x/3*math.pi))*2/3+(150*math.sin(x/12*math.pi)+300*math.sin(x/30*math.pi))*2/3
    rad = math.radians(lat)
    magic = 1-0.00669342162296594323*math.sin(rad)**2
    dlat = dlat*180/((6378245*(1-0.00669342162296594323)/(magic*math.sqrt(magic)))*math.pi)
    dlon = dlon*180/(6378245/math.sqrt(magic)*math.cos(rad)*math.pi)
    return lon+dlon, lat+dlat


def convert(lon, lat, source, dest='WGS84'):
    if source not in SYSTEMS or dest not in SYSTEMS or not valid(lon, lat):
        raise ValueError('坐标或坐标系无效')
    if source == dest:
        return lon, lat
    if source == 'BD-09':
        x, y = lon-0.0065, lat-0.006
        z = math.hypot(x, y)-0.00002*math.sin(y*math.pi*3000/180)
        t = math.atan2(y,x)-0.000003*math.cos(x*math.pi*3000/180)
        lon, lat = z*math.cos(t), z*math.sin(t)
        source = 'GCJ-02'
    if source == 'GCJ-02':
        glon, glat = lon, lat
        for _ in range(8):
            a,b = wgs_gcj(glon,glat)
            glon -= a-lon
            glat -= b-lat
        lon,lat = glon,glat
    if dest == 'WGS84':
        return lon,lat
    lon,lat = wgs_gcj(lon,lat)
    if dest == 'GCJ-02':
        return lon,lat
    z=math.hypot(lon,lat)+0.00002*math.sin(lat*math.pi*3000/180)
    t=math.atan2(lat,lon)+0.000003*math.cos(lon*math.pi*3000/180)
    return z*math.cos(t)+0.0065,z*math.sin(t)+0.006
