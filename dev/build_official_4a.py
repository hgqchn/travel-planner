#!/usr/bin/env python3
"""Rebuild the dated official 4A baseline, without touching any application DB.

Requires openpyxl and pypdf for reading downloaded Excel/PDF documents.
An installed LibreOffice executable is needed only to convert the Guangdong XLS.
No dependency is installed by this script. See resources/scenic/README-4a.md.
"""
from pathlib import Path
from html.parser import HTMLParser
import argparse, collections, datetime, json, os, re, shutil, subprocess
import urllib.request, urllib.parse

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / 'resources' / 'scenic'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--cache-dir', type=Path, default=Path('/tmp/travel-planner-scenic4a'))
parser.add_argument('--download-missing', action='store_true', help='Read missing official source files over the network.')
parser.add_argument('--output', type=Path, default=CATALOGUE / '4a-official.json')
parser.add_argument('--soffice', default=shutil.which('soffice'), help='LibreOffice executable, required only for legacy XLS conversion.')
args = parser.parse_args()
P = args.cache_dir.resolve()
P.mkdir(parents=True, exist_ok=True)
OUT = args.output
source_index = json.loads((CATALOGUE / '4a-sources.json').read_text())
links = source_index['links']

class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], None, None
    def handle_starttag(self, tag, attrs):
        if tag == 'tr': self.row = []
        if tag in ('td', 'th'): self.cell = []
    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            if self.row is not None: self.row.append(' '.join(''.join(self.cell).split()))
            self.cell = None
        if tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None
    def handle_data(self, text):
        if self.cell is not None: self.cell.append(text)

for filename, url in source_index['downloads'].items():
    path = P / filename
    if not path.is_file():
        if not args.download_missing:
            raise SystemExit(f'Missing {path}. Pass --download-missing or use the original source cache.')
        url = urllib.parse.quote(url, safe=':/?=&%#')
        req = urllib.request.Request(url, headers={'User-Agent': 'travel-planner-official-catalogue/1.0'})
        with urllib.request.urlopen(req, timeout=40) as response:
            body = response.read(20_000_001)
        if len(body) > 20_000_000: raise SystemExit(f'Source file exceeds size limit: {filename}')
        temp = path.with_suffix(path.suffix + '.tmp')
        temp.write_bytes(body)
        temp.replace(path)
    if filename.endswith('.html'):
        document = path.read_text(encoding='utf-8')
        table_parser = TableParser()
        table_parser.feed(document)
        (P / (path.stem + '.rows.json')).write_text(json.dumps(table_parser.rows, ensure_ascii=False))

legacy = P / 'guangdong.xls'
converted = P / 'guangdong.xlsx'
if not converted.is_file():
    if not args.soffice: raise SystemExit('Guangdong uses XLS. Provide an existing LibreOffice with --soffice; no packages are installed.')
    profile = (P / 'libreoffice-profile').as_uri()
    subprocess.run([args.soffice, '-env:UserInstallation=' + profile, '--headless', '--convert-to', 'xlsx', '--outdir', str(P), str(legacy)], check=True)

try:
    import openpyxl
    from pypdf import PdfReader
except ImportError as error:
    raise SystemExit('Use an existing Python environment containing openpyxl and pypdf. No packages are installed.') from error
for filename in list(source_index['downloads']) + ['guangdong.xlsx']:
    path = P / filename
    if path.suffix == '.xlsx':
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for i, sheet in enumerate(workbook):
            rows = [[str(v) if v is not None else '' for v in row] for row in sheet.iter_rows(max_col=12, values_only=True)]
            (P / (path.stem + f'-sheet{i}.rows.json')).write_text(json.dumps(rows, ensure_ascii=False))
        workbook.close()
    elif path.suffix == '.pdf':
        text = '\n'.join(page.extract_text(extraction_mode='layout') for page in PdfReader(path).pages)
        (P / (path.stem + '.txt')).write_text(text)

items, sources = [], []

def clean(s):return re.sub(r'\s+',' ',str(s or '')).strip()
def norm(s):return clean(s).upper().replace('Ａ','A').replace('４','4').replace('级','')
def four(s):return norm(s) in ('4A','AAAA')
def js(f):return json.loads((P/f).read_text())
def city(s):
 s=clean(s)
 # Only strip lower-level subdivisions when a city/league/prefecture is explicit.
 m=re.match(r'^(.{2,15}?(?:市|自治州|地区|盟|州))',s)
 return m.group(1) if m else s
def add(n,p,c,u,date):
 n=clean(n)
 if not n or len(n)>100:raise ValueError(('badname',n))
 d={'name':n,'province':p,'rating':'4A','source_url':u,'as_of':date}
 if c:d['city']=city(c)
 items.append(d)
def source(p,u,date,note,count):sources.append({'province':p,'source_url':u,'as_of':date,'coverage_note':note,'count':count})
def table(file,p,idx,namecol,citycol,gradecol,date,note='官方全A级名录中明确标为4A的记录。'):
 u=links[idx]['url'] if isinstance(idx,int) else idx; before=len(items);last=''
 for r in js(file):
  if len(r)<=max(namecol,gradecol,citycol if citycol is not None else 0):continue
  if citycol is not None and r[citycol]:last=r[citycol]
  if four(r[gradecol]):add(r[namecol],p,p if p in ('北京市','天津市','上海市','重庆市') else last,u,date)
 source(p,u,date,note,len(items)-before)
table('zhejiang-sheet0.rows.json','浙江省',15,0,1,3,'2024-12-31')
for r in items:
 if r['name']=='浙江省杭州市富阳区景区名称杭州野生动物世界景区':
  r['original_name']=r['name']
  r['name']='富阳区杭州野生动物世界景区'
  r['aliases']=['杭州野生动物世界景区','杭州野生动物世界']
  r['name_verification_url']='https://ct.zj.gov.cn/art/2020/11/6/art_1229678764_4984197.html'
table('liaoning-sheet0.rows.json','辽宁省',8,3,1,4,'2025-12')
table('hebei-sheet0.rows.json','河北省',4,1,2,3,'2026-01')
table('heilongjiang-sheet0.rows.json','黑龙江省',10,1,2,3,'2023-12-31')
table('source-23-sheet0.rows.json','福建省',23,1,2,4,'2025-12-31')
table('shandong-sheet0.rows.json','山东省',26,1,2,4,'2025-12-31')
table('guangdong-sheet0.rows.json','广东省',38,1,2,3,'2025-09-01')
table('shaanxi-sheet0.rows.json','陕西省',47,2,3,4,'2024-09-03','官方附件更新时间2024-09-03；页面初次发布2024-01-03。')
table('xinjiang-sheet0.rows.json','新疆维吾尔自治区',52,3,1,4,'2025-06-03')
table('source-09.rows.json','吉林省',9,1,2,3,'2025-11-20')
table('source-27.rows.json','河南省',27,1,2,3,'2020-12-31','官方2020年底基础名录；后续增补及等级调整尚未全部核验。')
table('source-42.rows.json','四川省',42,1,2,4,'2026-01-07')
table('source-43.rows.json','贵州省',43,2,1,3,'2026-02-09')
table('source-46.rows.json','西藏自治区',46,1,None,2,'2025-10-23')
table('source-49.rows.json','甘肃省',49,1,2,3,'2024-06-30')
table('source-51.rows.json','宁夏回族自治区',51,2,1,3,'2024-12-12')
table('qinghai.rows.json','青海省',50,1,3,2,'2026-06-23','官方全A级目录页面更新于2026-06-23；城市从原表地址的明确行政区前缀取得。')
for r in items:
 if r['province']=='青海省' and 'city' in r:
  m=re.match(r'^(.{2,15}?(?:市|自治州|地区|盟|州|县))',r['city'])
  if m:r['city']=m.group(1)
  else:r.pop('city')
table('guangxi-base.rows.json','广西壮族自治区','https://wlt.gxzf.gov.cn/zfxxgk/fdzdgknr/tzgg/t11739994.shtml',1,2,3,'2021-12-31','官方2021年底全量基础名录；后续增补和等级调整尚未全部核验。')
# Shanghai and Jiangxi publish dedicated 4A tables without a separate rating column.
before=len(items)
for r in js('shanghai.rows.json')[1:]:
 if len(r)==3 and r[0].isdigit():add(r[1],'上海市','上海市',links[13]['url'],'2025-12-16')
source('上海市',links[13]['url'],'2025-12-16','官方4A级景区专表。',len(items)-before)
before=len(items);last=''
for r in js('jiangxi-sheet1.rows.json')[1:]:
 if r[0]:last=r[0]
 if r[1]:add(r[1],'江西省',last,links[25]['url'],'2025-07-22')
source('江西省',links[25]['url'],'2025-07-22','官方Excel的4A景区工作表。',len(items)-before)
# Public application APIs, using only the fields displayed by the official pages.
before=len(items);j=js('beijing.json');assert len(j['data']['list'])==j['data']['total']
for r in j['data']['list']:
 if four(r.get('starLevel')):add(r['name'],'北京市','北京市',links[2]['url'],None)
source('北京市',links[2]['url'],None,'官方实时公开查询目录；未给整体统计截止日期，检索于2026-09-10。',len(items)-before)
before=len(items);j=js('tianjin.json')['page'];assert len(j['content'])==int(j['total'])
for r in j['content']:
 if four(r.get('JQJB')):add(r['DOCTITLE'],'天津市','天津市',links[3]['url'],'2026-03-05')
source('天津市',links[3]['url'],'2026-03-05','官方全A级目录公开查询接口。',len(items)-before)
before=len(items)
for r in js('hubei.json')['data']:
 if four(r.get('ZLDJ')):add(r['JQMC'],'湖北省',r['DQ'],links[33]['url'],None)
source('湖北省',links[33]['url'],None,'官方全A级目录公开JSON；未标整体截止日期，检索于2026-09-10。',len(items)-before)
# Chongqing embeds the published table as JSON in its government webpage.
before=len(items)
for line in (P/'source-41.html').read_text().splitlines():
 if line.startswith('{"index":'):
  r=json.loads(line.rstrip(','))
  if four(r['level']):add(r['name'],'重庆市','重庆市',links[41]['url'],'2025-12')
source('重庆市',links[41]['url'],'2025-12','官方全A级目录内嵌JSON。',len(items)-before)
# PDF text extraction preserves columns; require explicit rating, name and city.
before=len(items);current=''
for line in (P/'source-05.txt').read_text().splitlines():
 m=re.match(r'^\s*([^\s]+市)[：:]',line)
 if m:current=m.group(1)
 m=re.match(r'^\s*\d+\s+(.+?)\s{2,}4A(?:\s|$)',line)
 if m:add(m.group(1),'山西省',current,links[5]['url'],'2025-01')
source('山西省',links[5]['url'],'2025-01','官方PDF全A级名录。',len(items)-before)
before=len(items)
for line in (P/'nmg.txt').read_text().splitlines():
 m=re.match(r'^\s*\d+\s+(.+?)\s{2,}([^\s]+)\s+4A?\s*$',line)
 if m:add(m.group(1),'内蒙古自治区',m.group(2),links[7]['url'],'2024-02-26')
source('内蒙古自治区',links[7]['url'],'2024-02-26','官方PDF全A级名录；蒙牛工业旅游区原表等级为4（未显示A），按其在4A级分组中记录。',len(items)-before)
before=len(items)
u='https://dct.yn.gov.cn/ggfw/wldt/202510/t20251023_1654340.html'
for line in (P/'yunnan.txt').read_text().splitlines():
 m=re.match(r'^\s*\d+\s+(.+?)\s{2,}([^\s]+)\s+4A(?:\s|$)',line)
 if m:add(m.group(1),'云南省',m.group(2),u,'2024-12-31')
source('云南省',u,'2024-12-31','官方PDF全A级名录，正文统计197家4A。',len(items)-before)
# Hunan publishes city-labelled prose lists with Chinese list punctuation.
before=len(items);text=(P/'source-34.html').read_text();text=re.sub(r'<[^>]+>','',text)
for m in re.finditer(r'([^\s<>]{2,12}(?:市|州))（\d+个）：([^。]+)。',text):
 for n in m.group(2).split('、'):add(n,'湖南省',m.group(1),links[34]['url'],'2022-12-31')
source('湖南省',links[34]['url'],'2022-12-31','湖南省政府《湖南简况》4A景区专表；后续增补及等级调整尚未全部核验。',len(items)-before)
# Supplements are produced from independently verified provincial sources.
for f in sorted((CATALOGUE / 'supplements').glob('supplement-*.json')):
 j=json.loads(f.read_text());before=len(items)
 for r in j['items']:
  add(r['name'],r['province'],r.get('city'),r.get('source_url',j['source_url']),r.get('as_of',j.get('as_of')))
  for key in ('aliases','original_name','name_verification_url','source_name'):
   if key in r:items[-1][key]=r[key]
 date=j.get('as_of');date=date if date and re.fullmatch(r'\d{4}-\d{2}(?:-\d{2})?',date) else None
 source(j['province'],j['source_url'],date,j.get('coverage_note','官方名录。'),len(items)-before)
 if j.get('sources'):sources[-1]['sources']=j['sources']
seen=set();unique=[]
for r in items:
 k=(r['province'],r.get('city',''),r['name'])
 if k not in seen:seen.add(k);unique.append(r)
items=unique
allp='北京市 天津市 河北省 山西省 内蒙古自治区 辽宁省 吉林省 黑龙江省 上海市 江苏省 浙江省 安徽省 福建省 江西省 山东省 河南省 湖北省 湖南省 广东省 广西壮族自治区 海南省 重庆市 四川省 贵州省 云南省 西藏自治区 陕西省 甘肃省 青海省 宁夏回族自治区 新疆维吾尔自治区'.split()
covered={r['province'] for r in items};missing=[p for p in allp if p not in covered]
out={'source_url':'https://www.mct.gov.cn/wlhdjl/hdjl_detail.html?laiyuan=0&mailId=466dc3f8ed82444b8b38fae06c6ad003&type=dflb','source_name':'省级文化和旅游主管部门及省级人民政府公开名录汇编','retrieved_at':'2026-09-10','as_of':None,'coverage_note':'依据各省官方名录提取的4A级景区基础目录，不是同一时点全国现行完整名录。各省日期见coverage_sources及逐条as_of，历史名单可能包含后续升级、降级或取消等级记录；使用时应与更新的5A级名录核对。未加入仅拟评定公示的景区。港澳台不适用本目录评级体系，新疆兵团单独评定的景区尚未完成独立全量核验。','coverage_sources':sources,'missing_provinces':missing,'partial_provinces':[p for p in ('安徽省','江苏省') if p in covered],'items':sorted(items,key=lambda r:(r['province'],r.get('city',''),r['name']))}
print('written',len(items),'provinces',len(covered),'missing',missing)
print(json.dumps(collections.Counter(r['province'] for r in items),ensure_ascii=False))

# Data validation and a separate human-review report; never alter ratings based
# on fuzzy names or infer that a component scenic area is the entire 5A area.
invalid_names = [r for r in items if len(r['name']) < 2 or re.search(r'景区名称|填报单位|^序号$|联系电话|^\d+$|https?://', r['name'])]
invalid_dates = [r for r in items if r.get('as_of') is not None and not re.fullmatch(r'\d{4}-\d{2}(?:-\d{2})?', r['as_of'])]
assert not invalid_names, ('Invalid scenic names', invalid_names)
assert not invalid_dates, ('Invalid dates', invalid_dates)
assert len({(r['province'], r.get('city', ''), r['name']) for r in items}) == len(items)
counts = collections.Counter(r['province'] for r in items)
declared = {'上海市': 71, '重庆市': 165, '湖南省': 152, '云南省': 197, '海南省': 41}
for province, expected in declared.items():
    assert counts[province] == expected, (province, counts[province], expected, 'Review the source edition before changing this baseline assertion.')
overlaps = []
five_path = CATALOGUE / '5a-official.json'
if five_path.exists():
    five = json.loads(five_path.read_text())
    for old in items:
        short_name = old['name']
        for prefix in (old['province'], old.get('city', '')):
            if prefix and short_name.startswith(prefix): short_name = short_name[len(prefix):]
        for newer in five['items']:
            if newer['province'] != old['province'] or newer['name'] == old['name']: continue
            new_short = newer['name']
            for prefix in (old['province'], old.get('city', ''), newer.get('city', '')):
                if prefix and new_short.startswith(prefix): new_short = new_short[len(prefix):]
            suffix = r'(?:旅游景区|风景名胜区|风景旅游区|风景区|旅游区|景区)$'
            old_core = re.sub(suffix, '', short_name)
            new_core = re.sub(suffix, '', new_short)
            contained = len(short_name) >= 4 and short_name in newer['name']
            same_core = len(old_core) >= 3 and old_core == new_core
            if contained or same_core:
                overlaps.append({
                    'province': old['province'], 'city_4a': old.get('city'),
                    'name_4a': old['name'], 'as_of_4a': old.get('as_of'), 'source_url_4a': old['source_url'],
                    'name_5a': newer['name'], 'as_of_5a': five.get('as_of'),
                    'source_url_5a': newer.get('source_url', five['source_url']),
                    'status': 'needs_identity_review',
                    'basis': '同省，去明确行政区前缀后名称包含或去景区通用后缀后的核心同名；仅候选，未据此合并或覆盖。'
                })
audit = {
    'reviewed_at': '2026-09-10', 'record_count': len(items), 'province_counts': dict(counts),
    'official_declared_counts_verified': declared, 'invalid_name_count': len(invalid_names),
    'invalid_date_count': len(invalid_dates), 'missing_provinces': missing,
    'partial_provinces': out['partial_provinces'], 'suspected_5a_upgrades': overlaps,
    'corrections': [{
        'original_name': '浙江省杭州市富阳区景区名称杭州野生动物世界景区',
        'corrected_name': '富阳区杭州野生动物世界景区',
        'reason': '2024原始Excel A8混入列头文字；用同一省厅2019名录校正名称，4A等级仍依据2024表。',
        'verification_url': 'https://ct.zj.gov.cn/art/2020/11/6/art_1229678764_4984197.html'
    }],
    'limits': [
        '安徽、江苏仅有部分官方记录，不表示取得全省完整名录。',
        '历史表可能包含后续升级、撤销、改名记录；疑似同名不等于同一景区范围。',
        '青海原表地址只明确县级时保留该县名；无法确定归属时省略city。',
        '新疆兵团独立名录尚未完成全量核验。'
    ]
}
OUT.parent.mkdir(parents=True, exist_ok=True)
for path, document in ((OUT, out), (OUT.with_name('4a-audit.json'), audit)):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)
