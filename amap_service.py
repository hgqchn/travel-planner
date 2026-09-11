"""Small, bounded AMap adapter. Provider results are returned without disk caching."""
import datetime as dt
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


class AMapError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def coordinate(value):
    if not isinstance(value, str) or not re.fullmatch(r'-?\d{1,3}(?:\.\d{1,8})?,-?\d{1,2}(?:\.\d{1,8})?', value):
        raise AMapError(400, '请先从搜索结果中确认地点。')
    lng, lat = map(float, value.split(','))
    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
        raise AMapError(400, '地点坐标超出范围。')
    return f'{lng:.6f},{lat:.6f}'


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError):
        return None


def text(value):
    return value if isinstance(value, str) else ''


def route_result(data, mode):
    route = data.get('data' if mode == 'bicycling' else 'route') or {}
    candidates = route.get('transits' if mode == 'transit' else 'paths') or []
    # Do not turn an upstream failure/missing duration into a zero-minute trip.
    candidates = [p for p in candidates if isinstance(p, dict) and number(p.get('duration')) is not None]
    if not candidates:
        raise AMapError(422, '高德未返回可用路线，请确认入口、出发时间，或更换交通方式。')
    path = min(candidates, key=lambda p: number(p['duration']))
    steps = []
    incomplete = False
    if mode == 'transit':
        for segment in path.get('segments') or []:
            steps.extend((segment.get('walking') or {}).get('steps') or [])
            lines = (segment.get('bus') or {}).get('buslines') or []
            if lines:
                line = lines[0]
                steps.append({**line, 'instruction': '乘坐 ' + text(line.get('name'))})
            if segment.get('railway') or segment.get('taxi'):
                incomplete = True
    else:
        steps = path.get('steps') or []
    parts, instructions = [], []
    for step in steps:
        points = []
        for raw in text(step.get('polyline')).split(';'):
            if not raw:
                continue
            try:
                points.append(list(map(float, coordinate(raw).split(','))))
            except AMapError:
                incomplete = True
        if len(points) > 1:
            parts.append(points)
        else:
            incomplete = True
        instruction = text(step.get('instruction'))
        if instruction:
            instructions.append(instruction[:300])
    return {'duration': number(path['duration']), 'distance': number(path.get('distance')),
            'walking_distance': number(path.get('walking_distance')), 'parts': parts,
            'instructions': instructions[:100], 'incomplete': incomplete or not parts}


class AMapService:
    def __init__(self):
        self.js_key = os.environ.get('AMAP_JS_KEY', '').strip()
        self.security_code = os.environ.get('AMAP_SECURITY_JS_CODE', '').strip()
        self.web_key = os.environ.get('AMAP_WEB_SERVICE_KEY', '').strip()
        self.opener = urllib.request.build_opener(NoRedirect)
        self.lock = threading.Lock()
        self.last_call = 0.0
        self.day, self.count = '', 0
        try:
            self.daily_limit = max(1, int(os.environ.get('AMAP_DAILY_LIMIT', '300')))
        except ValueError:
            self.daily_limit = 300

    def config(self, project_id):
        return {'enabled': bool(self.js_key and self.security_code and self.web_key),
                'map_enabled': bool(self.js_key and self.security_code),
                'routing_enabled': bool(self.web_key), 'js_key': self.js_key,
                'service_host': f'/_AMapService/{project_id}', 'max_stops': 12}

    def fetch(self, host, path, params, *, metered=True):
        if metered:
            # Count attempts too; single process, resets at China midnight or restart.
            with self.lock:
                today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
                if today != self.day:
                    self.day, self.count = today, 0
                if self.count >= self.daily_limit:
                    raise AMapError(429, '本服务今日地图查询预算已用完，请明日再试或调整 AMAP_DAILY_LIMIT。')
                delay = max(0, 0.4 - (time.monotonic() - self.last_call))
                if delay:
                    time.sleep(delay)
                self.last_call = time.monotonic()
                self.count += 1
        url = f'https://{host}{path}?' + urllib.parse.urlencode(params)
        try:
            with self.opener.open(urllib.request.Request(url, headers={'User-Agent': 'TripPlanner/1.0'}), timeout=10) as response:
                body = response.read(4 * 1024 * 1024 + 1)
                if len(body) > 4 * 1024 * 1024:
                    raise AMapError(502, '地图服务返回内容过大，请缩短路线。')
                content_type = response.headers.get('Content-Type', 'application/octet-stream')
                return body, content_type
        except (urllib.error.URLError, TimeoutError, OSError):
            # Never surface/log the upstream URL containing a private key.
            raise AMapError(502, '无法连接高德服务，请检查服务器网络后重试。') from None

    def api(self, path, params):
        if not self.web_key:
            raise AMapError(503, '尚未配置高德 Web 服务 Key，请按 AMAP_SETUP.md 配置并重启。')
        body, _ = self.fetch('restapi.amap.com', path, {**params, 'key': self.web_key, 'output': 'JSON'})
        try:
            data = json.loads(body)
        except (ValueError, UnicodeError):
            raise AMapError(502, '高德返回格式异常，请稍后重试。') from None
        success = isinstance(data, dict) and (str(data.get('errcode')) == '0' if path == '/v4/direction/bicycling' else str(data.get('status')) == '1')
        if not success:
            code = str(data.get('infocode') or data.get('errcode', '')) if isinstance(data, dict) else ''
            hints = {'10001': 'Key 无效', '10005': '服务器出口 IP 不在白名单内',
                     '10009': 'Key 类型错误，请使用 Web 服务 Key', '10003': '调用量超限',
                     '10004': '调用过于频繁', '10044': '接口权限或账号授权不足'}
            safe_code = code if re.fullmatch(r'\d{5}', code) else '未知'
            raise AMapError(502, f'高德请求失败（{safe_code}）：{hints.get(code, "请检查控制台权限、配额及参数")}。')
        return data

    def search(self, payload, city):
        query = payload.get('keywords')
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 100:
            raise AMapError(400, '请输入 1–100 字的具体地点名称。')
        data = self.api('/v3/place/text', {'keywords': query.strip(), 'city': city,
                        'citylimit': 'true', 'offset': 5, 'page': 1, 'extensions': 'base'})
        results = []
        for poi in (data.get('pois') or [])[:5]:
            try:
                location = coordinate(poi.get('location'))
            except AMapError:
                continue
            results.append({'id': text(poi.get('id')), 'name': text(poi.get('name')),
                            'address': ' '.join(filter(None, [text(poi.get(k)) for k in ('cityname', 'adname', 'address')])),
                            'location': location})
        return {'places': results}

    def route(self, payload, city):
        mode = payload.get('mode')
        if mode not in ('walking', 'driving', 'transit', 'bicycling'):
            raise AMapError(400, '请选择步行、骑行、驾车或公交地铁。')
        params = {'origin': coordinate(payload.get('origin')), 'destination': coordinate(payload.get('destination')),
                  'extensions': 'all'}
        if mode == 'transit':
            try:
                date = dt.date.fromisoformat(payload.get('date', ''))
                departure = dt.time.fromisoformat(payload.get('time', ''))
            except (ValueError, TypeError):
                raise AMapError(400, '公交规划需要有效的出发日期和时间。') from None
            params.update(city=city, cityd=city, date=date.isoformat(), time=departure.strftime('%H:%M'), strategy='0', nightflag='1')
        path = '/v3/direction/' + ('transit/integrated' if mode == 'transit' else mode)
        if mode == 'bicycling':
            path = '/v4/direction/bicycling'
            params.pop('extensions', None)
        return route_result(self.api(path, params), mode)

    def proxy(self, path, query):
        if not self.js_key or not self.security_code:
            raise AMapError(503, '尚未配置高德 JS API。')
        # Only map bootstrap/style services. POI/routing go through our bounded API.
        if path not in ('/v4/maps', '/v4/map/styles', '/v3/log/init') or len(query) > 12000:
            raise AMapError(404, '不支持的地图代理请求。')
        params = dict(urllib.parse.parse_qsl(query, max_num_fields=60))
        callback = params.get('callback', '')
        if callback and not re.fullmatch(r'[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*', callback, re.ASCII):
            raise AMapError(400, '地图回调无效。')
        params.update(key=self.js_key, jscode=self.security_code)
        host = 'webapi.amap.com' if path == '/v4/map/styles' else 'restapi.amap.com'
        return self.fetch(host, path, params)
