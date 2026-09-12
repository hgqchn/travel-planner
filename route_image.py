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


def png_pixels(png):
    """Decode bounded, non-interlaced 8-bit static-map PNGs to RGB."""
    if not png.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('地图底图不是 PNG')
    offset, compressed, palette = 8, bytearray(), b''
    width = height = channels = 0
    while offset + 12 <= len(png):
        length = struct.unpack('!I', png[offset:offset+4])[0]
        kind = png[offset+4:offset+8]
        body = png[offset+8:offset+8+length]
        if len(body) != length:
            raise ValueError('地图底图不完整')
        if kind == b'IHDR':
            width, height, depth, mode, compression, filtering, interlace = struct.unpack('!2I5B', body)
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(mode, 0)
            if (width, height) != (1000, 600) or depth != 8 or not channels or compression or filtering or interlace:
                raise ValueError('地图底图格式不受支持')
        elif kind == b'PLTE':
            palette = body
        elif kind == b'IDAT':
            compressed.extend(body)
        elif kind == b'IEND':
            break
        offset += length + 12
    if not channels:
        raise ValueError('地图底图缺少尺寸')
    stride = width * channels
    expected = (stride + 1) * height
    decoder = zlib.decompressobj()
    raw = decoder.decompress(compressed, expected + 1)
    if len(raw) != expected or not decoder.eof:
        raise ValueError('地图底图像素数据异常')
    pixels, previous = bytearray(), bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        filter_type = raw[start]
        row = bytearray(raw[start+1:start+1+stride])
        if filter_type not in range(5):
            raise ValueError('地图底图滤波格式异常')
        for x in range(stride):
            a = row[x-channels] if x >= channels else 0
            b = previous[x]
            c = previous[x-channels] if x >= channels else 0
            if filter_type == 4:
                p = a + b - c
                distances = (abs(p-a), abs(p-b), abs(p-c))
                predictor = (a, b, c)[distances.index(min(distances))]
            else:
                predictor = (0, a, b, (a+b)//2)[filter_type]
            row[x] = (row[x] + predictor) & 255
        for x in range(0, stride, channels):
            if mode == 3:
                rgb = palette[row[x]*3:row[x]*3+3]
                if len(rgb) != 3:
                    raise ValueError('地图底图调色板异常')
            elif mode in (0, 4):
                rgb = bytes([row[x]]) * 3
            else:
                rgb = row[x:x+3]
            if mode in (4, 6):
                alpha = row[x+channels-1]
                rgb = bytes((n*alpha + 255*(255-alpha))//255 for n in rgb)
            pixels.extend(rgb)
        previous = row
    return pixels


def route_map(plan, *, background=None, viewport=None):
    visits = route_stops(plan)
    names = {v['id']:(i+1,stop_name(v)) for i,v in enumerate(visits)}
    routes = sorted([l for l in plan.get('routes',[]) if l.get('result')],key=lambda l:names.get(l['from_ref'],(999,''))[0])
    if not routes and background is None:
        return None
    paths = []
    for index, leg in enumerate(routes):
        for part in leg['result'].get('parts',[]):
            points = [p for p in part if isinstance(p,(list,tuple)) and len(p)==2 and all(type(n) in (int,float) and math.isfinite(n) for n in p)]
            if len(points)>1:
                paths.append((leg.get('color', COLORS[index%len(COLORS)]),points))
    if not paths and background is None:
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
    pixels = png_pixels(background) if background is not None else bytearray(bytes((247,249,252))*(width*height))
    def dot(x,y,color,radius=1):
        for yy in range(max(0,y-radius),min(height,y+radius+1)):
            for xx in range(max(0,x-radius),min(width,x+radius+1)):
                if (xx-x)**2+(yy-y)**2<=radius**2:
                    at=(yy*width+xx)*3; pixels[at:at+3]=bytes(color)
    def line(a,b,color,radius=2):
        x,y=a; dx=b[0]-x; dy=b[1]-y; steps=max(abs(dx),abs(dy),1)
        for i in range(steps+1):
            dot(round(x+dx*i/steps),round(y+dy*i/steps),color,radius)
    if background is None:
        for x in range(0,width,50): line((x,0),(x,height-1),(229,234,241),0)
        for y in range(0,height,50): line((0,y),(width-1,y),(229,234,241),0)
    cos=max(.01,math.cos(math.radians(sum(p[1] for p in coords)/len(coords))))
    xmin,xmax=min(p[0]*cos for p in coords),max(p[0]*cos for p in coords)
    ymin,ymax=min(p[1] for p in coords),max(p[1] for p in coords)
    scale=min((width-100)/max(xmax-xmin,.0001),(height-100)/max(ymax-ymin,.0001))
    def project(p):
        if viewport:
            x = (p[0]+180)/360
            y = (1-math.asinh(math.tan(math.radians(p[1])))/math.pi)/2
            size = 256 * 2**viewport['zoom']
            return round(width/2+(x-viewport['center'][0])*size), round(height/2+(y-viewport['center'][1])*size)
        return round(width/2+(p[0]*cos-(xmin+xmax)/2)*scale),round(height/2-(p[1]-(ymin+ymax)/2)*scale)
    for color,points in paths:
        for a,b in zip(points,points[1:]): line(project(a),project(b),color,3)
    grouped_pins = {}
    for number,p in pins:
        grouped_pins.setdefault(tuple(p), []).append(str(number))
    for p,labels in grouped_pins.items():
        label='/'.join(labels)
        radius=max(14,len(label)*4+2)
        x,y=project(p); dot(x,y,(255,255,255),radius+3); dot(x,y,(25,47,70),radius)
        for pos,digit in enumerate(label):
            for i,on in enumerate('001001010100100' if digit=='/' else DIGITS[int(digit)]):
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
