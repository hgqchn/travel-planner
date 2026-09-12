"""Offline route geometry rendered as a portable PNG using the standard library."""
import math
import struct
import zlib
import hashlib
import json

COLORS = [(36,88,189),(189,75,24),(18,129,95),(135,80,189),(189,55,101)]
DIGITS = ['111101101101111','010110010010111','111001111100111','111001111001111',
          '101101111001001','111100111001111','111100111101111','111001001001001',
          '111101111101111','111101111001111']


def stop_name(stop):
    return (stop.get('poi') or {}).get('name') or stop.get('location') or stop.get('title', '')


def route_stops(plan):
    visits = [v for v in plan.get('visits',[]) if not v.get('is_backup')]
    for key, ref, first in [('start_anchor','@start',True),('end_anchor','@end',False)]:
        poi = plan['settings'].get(key)
        if poi:
            node = {'id':ref,'poi':poi,'title':poi['name']}
            visits.insert(0,node) if first else visits.append(node)
    return visits


def preview(plan):
    visits = route_stops(plan)
    routes = [l for l in plan.get('routes',[]) if l.get('result')]
    if not any(len(part)>1 for leg in routes for part in leg['result'].get('parts',[])):
        return None
    stops = [dict(number=i+1,name=stop_name(v),poi=v.get('poi')) for i,v in enumerate(visits)]
    digest = hashlib.sha256(json.dumps([routes,stops],ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:24]
    moving = [v for v in visits if not (v.get('visit_kind')=='rest' and not v.get('poi'))]
    return dict(version=digest, planned=len(routes), total=max(0,len(moving)-1),
                minutes=sum(math.ceil(l['result']['duration']/60) for l in routes),
                stops=[dict(number=s['number'],name=s['name']) for s in stops],
                incomplete=any(l['result'].get('incomplete') for l in routes))


def route_map(plan):
    visits = route_stops(plan)
    names = {v['id']:(i+1,stop_name(v)) for i,v in enumerate(visits)}
    routes = sorted([l for l in plan.get('routes',[]) if l.get('result')],key=lambda l:names.get(l['from_ref'],(999,''))[0])
    if not routes:
        return None
    paths = []
    for index, leg in enumerate(routes):
        for part in leg['result'].get('parts',[]):
            points = [p for p in part if isinstance(p,(list,tuple)) and len(p)==2 and all(type(n) in (int,float) and math.isfinite(n) for n in p)]
            if len(points)>1:
                paths.append((index,points))
    if not paths:
        return None
    coords = [p for _,points in paths for p in points]
    pins = []
    for v in visits:
        try:
            point = [float(n) for n in v['poi']['location'].split(',')]
            if len(point)==2 and all(math.isfinite(n) for n in point):
                pins.append((names[v['id']][0],point)); coords.append(point)
        except (KeyError,TypeError,ValueError):
            pass
    width,height = 1000,600
    pixels = bytearray(bytes((247,249,252))*(width*height))
    def dot(x,y,color,radius=1):
        for yy in range(max(0,y-radius),min(height,y+radius+1)):
            for xx in range(max(0,x-radius),min(width,x+radius+1)):
                if (xx-x)**2+(yy-y)**2<=radius**2:
                    at=(yy*width+xx)*3; pixels[at:at+3]=bytes(color)
    def line(a,b,color,radius=2):
        x,y=a; dx=b[0]-x; dy=b[1]-y; steps=max(abs(dx),abs(dy),1)
        for i in range(steps+1):
            dot(round(x+dx*i/steps),round(y+dy*i/steps),color,radius)
    for x in range(0,width,50): line((x,0),(x,height-1),(229,234,241),0)
    for y in range(0,height,50): line((0,y),(width-1,y),(229,234,241),0)
    cos=max(.01,math.cos(math.radians(sum(p[1] for p in coords)/len(coords))))
    xmin,xmax=min(p[0]*cos for p in coords),max(p[0]*cos for p in coords)
    ymin,ymax=min(p[1] for p in coords),max(p[1] for p in coords)
    scale=min((width-100)/max(xmax-xmin,.0001),(height-100)/max(ymax-ymin,.0001))
    def project(p):
        return round(width/2+(p[0]*cos-(xmin+xmax)/2)*scale),round(height/2-(p[1]-(ymin+ymax)/2)*scale)
    for index,points in paths:
        for a,b in zip(points,points[1:]): line(project(a),project(b),COLORS[index%len(COLORS)],3)
    for number,p in pins:
        x,y=project(p); dot(x,y,(255,255,255),17); dot(x,y,(25,47,70),14)
        label=str(number)
        for pos,digit in enumerate(label):
            for i,on in enumerate(DIGITS[int(digit)]):
                if on=='1':
                    for dx in range(2):
                        for dy in range(2): dot(x-len(label)*4+pos*8+(i%3)*2+dx,y-5+(i//3)*2+dy,(255,255,255),0)
    def chunk(kind,body):
        return struct.pack('!I',len(body))+kind+body+struct.pack('!I',zlib.crc32(kind+body)&0xffffffff)
    raw=b''.join(b'\0'+pixels[y*width*3:(y+1)*width*3] for y in range(height))
    png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')
    legend=[f'{number}. {label}' for number,label in names.values()]
    modes={'walking':'步行','transit':'公交 / 地铁','driving':'驾车','bicycling':'骑行'}
    for leg in routes:
        a=names.get(leg['from_ref'],('',leg['from_ref'])); b=names.get(leg['to_ref'],('',leg['to_ref']))
        legend.append(f"{a[1]} → {b[1]} · {modes.get(leg['mode'],leg['mode'])}")
        if leg['result'].get('incomplete'): legend.append('此路段部分轨迹缺失。')
    return png,legend
