from pathlib import Path
import json
import csv
from openpyxl import Workbook
ROOT=Path(__file__).resolve().parents[1]
# Representative city-centre coordinates, GCJ-02; not administrative boundaries.
CITIES='''北京,116.407387,39.904179
天津,117.200983,39.084158
石家庄,114.514976,38.042007
太原,112.548879,37.870590
呼和浩特,111.749180,40.842585
沈阳,123.431474,41.805698
长春,125.323544,43.817071
哈尔滨,126.534967,45.803775
上海,121.473667,31.230525
南京,118.796624,32.059344
杭州,120.209947,30.245853
合肥,117.227239,31.820586
福州,119.296411,26.074286
南昌,115.858197,28.682892
济南,117.120128,36.652069
郑州,113.625351,34.746303
武汉,114.305469,30.593175
长沙,112.938882,28.228304
广州,113.264385,23.129110
南宁,108.366407,22.817700
海口,110.198293,20.044001
重庆,106.551556,29.563009
成都,104.066301,30.572961
贵阳,106.630153,26.647661
昆明,102.832891,24.880095
拉萨,91.140856,29.645554
西安,108.939645,34.343207
兰州,103.834228,36.060798
西宁,101.778228,36.617144
银川,106.230977,38.487783
乌鲁木齐,87.616848,43.825592'''
if __name__=='__main__':
    cities=[dict(name=n,lon=float(x),lat=float(y),crs='GCJ-02',note='内置城市中心代表点；非行政边界或精确政府驻地，研究前请核验。') for n,x,y in csv.reader(CITIES.splitlines())]
    (ROOT/'resources'/'capitals.json').write_text(json.dumps(cities,ensure_ascii=False,indent=2),encoding='utf-8')
    rows=[['ID','名称','经度','纬度']]+[[f'A{i+1:03}',x['name']+'测试点',x['lon'],x['lat']] for i,x in enumerate(cities[:12])]
    rows.extend([rows[1],['ERR1','错误经纬度',30.5,114.3],['ERR2','缺失纬度',114.3,None],['ERR3','非数字','未知',31.2],['EMPTY',None,None,None]])
    with (ROOT/'resources'/'test_points.csv').open('w',encoding='utf-8-sig',newline='') as f: csv.writer(f).writerows(rows)
    wb=Workbook(); ws=wb.active; ws.title='测试点位_GCJ02'
    for row in rows: ws.append(row)
    ws.freeze_panes='A2'; ws.auto_filter.ref=ws.dimensions
    for col in 'ABCD': ws.column_dimensions[col].width=22
    wb.save(ROOT/'resources'/'test_points.xlsx')
