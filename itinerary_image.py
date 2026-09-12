"""A minimal saved-itinerary snapshot for browser PNG composition."""
import base64
import json
import math
import zlib

from amap_service import AMapError
from itinerary_export import filename
from route_image import route_stops, stop_name, route_map

MODES = {'walking': '步行', 'transit': '公交 / 地铁', 'driving': '驾车', 'bicycling': '骑行'}
COLORS = ['#2458bd', '#bd4b18', '#12815f', '#8750bd', '#bd3765']


def point(value):
    try:
        values = [float(n) for n in value.split(',')] if isinstance(value, str) else list(value)
        if len(values) == 2 and all(type(n) in (int, float) and math.isfinite(n) for n in values) and abs(values[0]) <= 180 and abs(values[1]) <= 85:
            return values
    except (TypeError, ValueError):
        pass
    return None


def mercator(p):
    return [(p[0] + 180) / 360, (1 - math.asinh(math.tan(math.radians(p[1]))) / math.pi) / 2]


def build_image_data(data, amap):
    days, pins, paths, coords = [], [], [], []
    for plan in sorted(data['daily_plans'], key=lambda p: (p['date'], p.get('city_name', ''))):
        color = COLORS[len(days) % len(COLORS)]
        stops = route_stops(plan)
        moving = [s for s in stops if not (s.get('visit_kind') == 'rest' and not s.get('poi'))]
        labels = {}
        for stop in stops:
            location = point((stop.get('poi') or {}).get('location'))
            label = stop_name(stop)
            if location:
                number = len(pins) + 1
                pins.append(dict(point=location, number=number, color=color))
                coords.append(location)
                label = f'{number}. {label}'
            elif stop in moving:
                label += '（地点待确认）'
            labels[stop['id']] = label
        saved = {(r['from_ref'], r['to_ref']): r for r in plan.get('routes', []) if r.get('result')}
        segments = []
        for a, b in zip(moving, moving[1:]):
            route = saved.get((a['id'], b['id']))
            key = json.dumps([a['id'], b['id']], ensure_ascii=False, separators=(',', ':'))
            mode = route['mode'] if route else plan['settings'].get('leg_modes', {}).get(key, 'transit')
            segments.append(f"{labels[a['id']]} → {labels[b['id']]} · {MODES.get(mode, mode)}" + ('（待规划）' if not route else ''))
            for part in (route or {}).get('result', {}).get('parts', []):
                # Split at invalid points instead of connecting across missing geometry.
                run = []
                for raw in [*part, None]:
                    p = point(raw)
                    if p is not None:
                        run.append(p)
                    else:
                        if len(run) > 1:
                            paths.append(dict(points=run, color=color))
                            coords.extend(run)
                        run = []
        days.append(dict(date=plan['date'], city=plan.get('city_name', ''), color=color,
                         stops=[labels[s['id']] for s in stops], segments=segments,
                         backups=[s.get('title', '') for s in plan['visits'] if s.get('is_backup')]))
    if not coords:
        raise AMapError(400, '还没有已确认的地图地点，请先在路线规划中确认起点和终点后再导出图片。')
    projected = [mercator(p) for p in coords]
    low = [min(p[i] for p in projected) for i in range(2)]
    high = [max(p[i] for p in projected) for i in range(2)]
    center = [(low[i] + high[i]) / 2 for i in range(2)]
    zoom = max(1, min(17, math.floor(math.log2(min(880 / max(high[0]-low[0], 1e-10), 480 / max(high[1]-low[1], 1e-10)) / 256))))
    location = f'{center[0]*360-180:.6f},{math.degrees(math.atan(math.sinh(math.pi*(1-2*center[1])))):.6f}'
    image, mime = amap.static_map(location, zoom)
    return dict(filename=filename(data, 'png'), title=data['project_name'], scope=data['scope_name'],
                days=days, map=dict(image=f'data:{mime};base64,' + base64.b64encode(image).decode('ascii'),
                                    center=mercator(point(location)), zoom=zoom, pins=pins, paths=paths))


def build_office_map(data, amap):
    """One embedded map covering every day in the selected export scope."""
    if not any(point((s.get('poi') or {}).get('location'))
               for p in data.get('daily_plans', []) for s in route_stops(p)):
        return None
    snapshot = build_image_data(data, amap)
    spec = snapshot['map']
    visits = [dict(id=str(pin['number']), title=str(pin['number']),
                   poi=dict(location=','.join(map(str, pin['point'])))) for pin in spec['pins']]
    routes = [dict(from_ref='', to_ref='', mode='walking',
                   color=tuple(int(path['color'][i:i+2], 16) for i in (1, 3, 5)),
                   result=dict(parts=[path['points']], duration=0)) for path in spec['paths']]
    try:
        png, _ = route_map(dict(settings={}, visits=visits, routes=routes),
                           background=base64.b64decode(spec['image'].split(',', 1)[1]), viewport=spec)
    except (ValueError, IndexError, zlib.error) as error:
        raise AMapError(502, '整体行程地图生成失败，请重试。') from error
    legend = []
    for day in snapshot['days']:
        legend.append(f"{day['date']} · {day['city']}")
        legend.extend(day['segments'] or day['stops'])
    return png, legend


def build_office_maps(data, amap):
    """Build an overview followed by one map per date (including all its cities)."""
    overall = build_office_map(data, amap)
    dates = sorted({plan['date'] for plan in data.get('daily_plans', [])})
    daily = {}
    for day in dates:
        daily[day] = overall if len(dates) == 1 else build_office_map(
            {**data, 'daily_plans': [p for p in data['daily_plans'] if p['date'] == day]}, amap)
    return dict(overall_route_map=overall, daily_route_maps=daily)


def build_image_bundle(data, amap):
    overall = build_image_data(data, amap)
    dates = sorted({p['date'] for p in data['daily_plans']})
    daily = []
    for day in dates:
        plans = [p for p in data['daily_plans'] if p['date'] == day]
        scoped = {**data, 'daily_plans': plans}
        if not any(point((s.get('poi') or {}).get('location')) for p in plans for s in route_stops(p)):
            # A day without confirmed locations still gets its own image.
            item = dict(title=data['project_name'], scope=day, map=None, days=[dict(
                date=day, city=p.get('city_name', ''), color=COLORS[i % len(COLORS)],
                stops=[stop_name(s) for s in route_stops(p)], segments=[],
                backups=[s.get('title', '') for s in p['visits'] if s.get('is_backup')]) for i,p in enumerate(plans)])
        else:
            item = dict(overall) if len(dates) == 1 else build_image_data(scoped, amap)
        item['filename'] = f'{day}-当天行程.png'
        daily.append(item)
    return {**overall, 'filename': filename(data, 'zip'), 'daily_images': daily}
