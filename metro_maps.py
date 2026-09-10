"""Licensed local rail maps, with bounded, atomic refreshes from Wikimedia Commons."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCES = ROOT / 'metro_sources.json'
DEFAULT_BUNDLED = ROOT / 'public' / 'assets' / 'metro'
USER_AGENT = 'TravelPlannerMetroMap/1.0 (https://github.com/hgqchn/travel-planner)'
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
ID_PATTERN = re.compile(r'[a-z0-9][a-z0-9-]{0,63}')
VERSION_PATTERN = re.compile(r'[a-f0-9]{64}')


class MetroMapError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.message = message
        self.status = status


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data):
        self.parts.append(data)


def plain_text(value: Any, limit: int = 1200) -> str:
    parser = _PlainText()
    parser.feed(str(value or '')[:20000])
    return ' '.join(' '.join(parser.parts).split())[:limit]


def city_key(value: str) -> str:
    import unicodedata
    value = ''.join(unicodedata.normalize('NFKC', value).casefold().split())
    return value[:-1] if value.endswith('市') else value


def validate_remote_url(url: str, *, image: bool) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise MetroMapError('图源地址无效。') from error
    hosts = ('upload.wikimedia.org', 'thumb.wikimedia.org') if image else ('commons.wikimedia.org',)
    if (parsed.scheme != 'https' or parsed.hostname not in hosts or parsed.username or parsed.password
            or port not in (None, 443) or parsed.fragment
            or (image and not parsed.path.startswith('/wikipedia/commons/'))
            or (not image and parsed.path != '/w/api.php')):
        raise MetroMapError('图源地址不在允许范围内。')
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '' if image else parsed.query, ''))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Wikimedia imageinfo gives a direct original-file URL; never turn this
        # service into a general proxy, even if an upstream endpoint redirects.
        raise MetroMapError('图源返回了意外跳转，已保留原线路图。')


def fetch_bytes(url: str, *, image: bool, limit: int) -> bytes:
    url = validate_remote_url(url, image=image)
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': '*/*' if image else 'application/json'})
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=20) as response:
            length = response.headers.get('Content-Length')
            if length and int(length) > limit:
                raise MetroMapError('图源文件过大，已保留原线路图。')
            result, size, deadline = [], 0, time.monotonic() + 90
            read = getattr(response, 'read1', response.read)
            while True:
                if time.monotonic() > deadline:
                    raise MetroMapError('下载线路图超时，请稍后重试。')
                chunk = read(min(65536, limit + 1 - size))
                if not chunk:
                    break
                result.append(chunk)
                size += len(chunk)
                if size > limit:
                    raise MetroMapError('图源文件过大，已保留原线路图。')
            return b''.join(result)
    except MetroMapError:
        raise
    except (OSError, ValueError, urllib.error.URLError) as error:
        raise MetroMapError('暂时无法连接图源，请稍后重试；已有线路图仍可使用。') from error


def validate_image(data: bytes, mime: str) -> tuple[str, int, int]:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise MetroMapError('线路图文件大小无效。')
    if mime == 'image/svg+xml':
        if b'<!ENTITY' in data.upper():
            raise MetroMapError('线路图包含不支持的 XML 内容。')
        try:
            root = ET.fromstring(data)
        except (ET.ParseError, ValueError) as error:
            raise MetroMapError('图源未返回有效的 SVG 线路图。') from error
        if root.tag != '{http://www.w3.org/2000/svg}svg':
            raise MetroMapError('图源未返回 SVG 线路图。')
        for node in root.iter():
            tag = node.tag.rsplit('}', 1)[-1].lower()
            if tag in ('script', 'foreignobject', 'iframe', 'object', 'embed', 'animate', 'set', 'animatetransform', 'animatemotion'):
                raise MetroMapError('线路图包含活动内容，已保留原图。')
            for key, value in node.attrib.items():
                attr = key.rsplit('}', 1)[-1].lower()
                if attr.startswith('on'):
                    raise MetroMapError('线路图包含活动内容，已保留原图。')
                if attr in ('href', 'src') and tag not in ('a', 'metadata'):
                    if value and not value.startswith('#') and not value.startswith(('data:image/png;base64,', 'data:image/jpeg;base64,')):
                        raise MetroMapError('线路图依赖外部图片，无法安全离线显示。')
                if external_css_reference(value):
                    raise MetroMapError('线路图依赖外部样式资源。')
            if tag == 'style':
                css = ''.join(node.itertext())
                if external_css_reference(css):
                    raise MetroMapError('线路图依赖外部样式资源。')
        box = re.split(r'[\s,]+', root.get('viewBox', '').strip())
        try:
            if len(box) == 4:
                width, height = int(float(box[2])), int(float(box[3]))
            else:
                width = int(float(re.sub(r'(px|pt|mm|cm|in)$', '', root.get('width', '0'))))
                height = int(float(re.sub(r'(px|pt|mm|cm|in)$', '', root.get('height', '0'))))
        except (ValueError, OverflowError) as error:
            raise MetroMapError('线路图尺寸无效。') from error
        extension = 'svg'
    elif mime == 'image/png' and data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) >= 33 and data[12:16] == b'IHDR' and b'IEND' in data[-16:]:
        width, height = struct.unpack('>II', data[16:24])
        extension = 'png'
    elif mime == 'image/jpeg' and data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9'):
        width = height = 0
        offset = 2
        while offset + 4 <= len(data):
            if data[offset] != 255:
                break
            marker = data[offset + 1]
            offset += 2
            if marker in (0xD8, 0xD9):
                continue
            length = int.from_bytes(data[offset:offset + 2], 'big')
            if length < 2 or offset + length > len(data):
                break
            if marker in (0xC0, 0xC1, 0xC2) and length >= 8:
                height, width = struct.unpack('>HH', data[offset + 3:offset + 7])
                break
            offset += length
        extension = 'jpg'
    else:
        raise MetroMapError('图源不是支持的 SVG、PNG 或 JPEG 文件。')
    if width <= 0 or height <= 0 or max(width, height) > 50000:
        raise MetroMapError('线路图尺寸无效。')
    if extension != 'svg' and (max(width, height) < 1600 or width * height > 100_000_000):
        raise MetroMapError('该图片未达到高清要求，或像素数量过大。')
    return extension, width, height


def external_css_reference(css: str) -> bool:
    if '@import' in css.lower():
        return True
    for match in re.finditer(r'url\(([^)]*)\)', css, re.I):
        target = match.group(1).strip().strip('\"\'').strip()
        if not target.startswith('#'):
            return True
    return False


def source_metadata(source: dict) -> dict:
    query = urllib.parse.urlencode({'action': 'query', 'format': 'json', 'prop': 'imageinfo',
            'titles': source['commons_title'], 'iiprop': 'url|size|timestamp|sha1|mime|extmetadata'})
    try:
        response = json.loads(fetch_bytes('https://commons.wikimedia.org/w/api.php?' + query,
                                          image=False, limit=MAX_METADATA_BYTES))
        page = next(iter(response['query']['pages'].values()))
        info = page['imageinfo'][0]
        metadata = info['extmetadata']
        value = lambda key: plain_text(metadata.get(key, {}).get('value', ''))
        license_name = value('LicenseShortName')
        taipei_attribution = (license_name == 'Attribution' and
            page['title'] == 'File:Taipei Metro official map optimised.png')
        if not (re.fullmatch(r'CC BY(?:-SA)? [1-4]\.0', license_name) or license_name in ('CC0', 'CC0 1.0', 'Public domain') or taipei_attribution):
            raise MetroMapError('暂无法确认该图的可再分发许可，已保留原图。')
        url = validate_remote_url(info['url'], image=True)
        if not 0 < info['size'] <= MAX_IMAGE_BYTES:
            raise MetroMapError('图源文件过大或无效。')
        mime = info['mime']
        if mime != 'image/svg+xml' and info.get('width', 0) * info.get('height', 0) > 100_000_000:
            # Ask the source for its own HD rendition instead of decoding a
            # hundreds-of-megapixels original on users' phones.
            thumb_query = query + '&iiurlwidth=6000'
            thumbs = json.loads(fetch_bytes('https://commons.wikimedia.org/w/api.php?' + thumb_query,
                                            image=False, limit=MAX_METADATA_BYTES))
            thumb = next(iter(thumbs['query']['pages'].values()))['imageinfo'][0]
            url = validate_remote_url(thumb['thumburl'], image=True)
            mime = thumb.get('thumbmime', mime)
        license_url = value('LicenseUrl')
        if license_url.startswith('//'):
            license_url = 'https:' + license_url
        if license_url.startswith('http://creativecommons.org/'):
            license_url = 'https://' + license_url[len('http://'):]
        if not license_url.startswith('https://creativecommons.org/'):
            license_url = 'https://commons.wikimedia.org/wiki/Commons:Licensing'
        source_url = 'https://commons.wikimedia.org/wiki/' + urllib.parse.quote(page['title'].replace(' ', '_'), safe=':')
        if taipei_attribution:
            license_url = source_url + '#Licensing'
        return {'source_url': source_url,
                'source_name': 'Wikimedia Commons', 'author': value('Artist') or value('Credit') or '见图源署名',
                'license': license_name, 'license_url': license_url, 'source_updated_at': info['timestamp'],
                'remote_sha1': info['sha1'], 'download_url': url, 'mime': mime}
    except MetroMapError:
        raise
    except (KeyError, TypeError, ValueError, StopIteration) as error:
        raise MetroMapError('无法读取该城市线路图的来源资料。') from error


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class MetroMapStore:
    def __init__(self, cache_dir: Path, *, bundled_dir: Path = DEFAULT_BUNDLED,
                 sources_path: Path = DEFAULT_SOURCES, cooldown_seconds: int = 300):
        self.cache_dir, self.bundled_dir = Path(cache_dir), Path(bundled_dir)
        catalog = json.loads(Path(sources_path).read_text(encoding='utf-8'))
        self.sources = {}
        self._aliases = {}
        for source in catalog['sources']:
            if not ID_PATTERN.fullmatch(source['id']) or not source['commons_title'].startswith('File:'):
                raise ValueError('Invalid metro map catalog entry')
            self.sources[source['id']] = source
            for name in [source['id'], source['name'], *source.get('aliases', [])]:
                self._aliases[city_key(name)] = source['id']
        self._bundled = {}
        index = self.bundled_dir / 'index.json'
        if index.is_file():
            self._bundled = {row['id']: row for row in json.loads(index.read_text(encoding='utf-8')).get('maps', [])}
        self._lock = threading.RLock()
        self._jobs: dict[str, dict] = {}
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='metro-map')
        self.cooldown_seconds = cooldown_seconds
        self._closed = False

    def source_for_city(self, name: str) -> dict | None:
        return self.sources.get(self._aliases.get(city_key(name)))

    def _metadata(self, source_id: str) -> dict | None:
        candidates = []
        try:
            path = self.cache_dir / (source_id + '.json')
            if path.is_file() and path.stat().st_size <= MAX_METADATA_BYTES:
                candidates.append(json.loads(path.read_text(encoding='utf-8')))
        except (OSError, ValueError):
            pass
        candidates.append(self._bundled.get(source_id))
        for item in candidates:
            if (isinstance(item, dict) and item.get('id') == source_id
                    and VERSION_PATTERN.fullmatch(str(item.get('version', '')))
                    and item.get('format') in ('svg', 'png', 'jpg')
                    and self.asset_path(source_id, item['version'], item['format'])):
                return item
        return None

    def asset_path(self, source_id: str, version: str, extension: str) -> Path | None:
        if source_id not in self.sources or not VERSION_PATTERN.fullmatch(version) or extension not in ('svg', 'png', 'jpg'):
            return None
        filename = f'{source_id}-{version}.{extension}'
        for directory in (self.cache_dir, self.bundled_dir):
            path = directory / filename
            if path.is_file() and not path.is_symlink() and path.resolve().parent == directory.resolve():
                return path
        return None

    def describe(self, city_id: str, city_name: str) -> dict:
        with self._lock:
            source = self.source_for_city(city_name)
            metadata = self._metadata(source['id']) if source else None
            public_map = None
            if metadata:
                fields = ('id', 'name', 'source_url', 'source_name', 'author', 'license', 'license_url',
                          'source_updated_at', 'checked_at', 'downloaded_at', 'format', 'width', 'height', 'version')
                public_map = {key: metadata.get(key, '') for key in fields}
                public_map['image_url'] = f"/api/metro-maps/image/{source['id']}/{metadata['version']}.{metadata['format']}"
                if source.get('official_url'):
                    public_map['official_url'] = source['official_url']
            job = self._jobs.get(source['id'], {}) if source else {}
            message = '社区维护的线路示意图，文件更新日期不代表实时运营状态。' if metadata else '已收录该城市图源，点击下载高清线路图。' if source else '暂未收录该城市的高清轨道交通图，可使用城市地图查询公共交通。'
            if source and source.get('note'):
                message += ' ' + source['note']
            return {'city_id': city_id, 'city_name': city_name, 'supported': bool(source),
                    'available': bool(public_map), 'map': public_map,
                    'update': {'status': job.get('status', 'idle'), 'error': job.get('error', '')}, 'message': message}

    def request_update(self, city_id: str, city_name: str) -> dict:
        with self._lock:
            source = self.source_for_city(city_name)
            if not source:
                raise MetroMapError('暂未收录该城市的可更新图源。', 404)
            if self._closed:
                raise MetroMapError('线路图服务正在重启，请稍后重试。', 503)
            old = self._jobs.get(source['id'])
            if old and old['status'] in ('queued', 'running'):
                return self.describe(city_id, city_name)
            if old and time.monotonic() - old['started'] < self.cooldown_seconds:
                raise MetroMapError('刚刚检查过这个城市的图源，请稍后再试。', 429)
            if sum(job['status'] in ('queued', 'running') for job in self._jobs.values()) >= 8:
                raise MetroMapError('正在更新其他城市的线路图，请稍后重试。', 429)
            self._jobs[source['id']] = {'status': 'queued', 'error': '', 'started': time.monotonic()}
            self._executor.submit(self._update_worker, source['id'])
            return self.describe(city_id, city_name)

    def _update_worker(self, source_id: str) -> None:
        with self._lock:
            self._jobs[source_id]['status'] = 'running'
        try:
            self.update_now(source_id)
        except Exception as error:
            message = error.message if isinstance(error, MetroMapError) else '更新未完成，已有线路图仍可使用，请稍后重试。'
            with self._lock:
                self._jobs[source_id].update(status='failed', error=message)
        else:
            with self._lock:
                self._jobs[source_id].update(status='ready', error='')

    def update_now(self, source_id: str) -> dict:
        """CLI refresh; HTTP callers use request_update for bounded background work."""
        source = self.sources[source_id]
        upstream = source_metadata(source)
        old = self._metadata(source_id)
        checked_at = now()
        if old and old.get('remote_sha1') == upstream['remote_sha1']:
            result = dict(old, **upstream, checked_at=checked_at)
        else:
            data = fetch_bytes(upstream['download_url'], image=True, limit=MAX_IMAGE_BYTES)
            extension, width, height = validate_image(data, upstream['mime'])
            version = hashlib.sha256(data).hexdigest()
            result = dict(upstream, id=source_id, name=source['name'], version=version,
                          format=extension, width=width, height=height, checked_at=checked_at, downloaded_at=checked_at)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            asset = self.cache_dir / f'{source_id}-{version}.{extension}'
            temporary = asset.with_name('.' + asset.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                temporary.write_bytes(data)
                temporary.replace(asset)
            finally:
                temporary.unlink(missing_ok=True)
        atomic_json(self.cache_dir / f'{source_id}.json', result)
        return result

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)


def main():
    parser = argparse.ArgumentParser(description='下载已核验图源目录中的全国高清轨道交通图')
    parser.add_argument('--sources', type=Path, default=DEFAULT_SOURCES)
    parser.add_argument('--output', type=Path, default=DEFAULT_BUNDLED)
    parser.add_argument('--cities', nargs='*', help='仅下载给定城市ID，省略则全部下载')
    args = parser.parse_args()
    store = MetroMapStore(args.output, bundled_dir=args.output, sources_path=args.sources, cooldown_seconds=0)
    failures = []
    for source_id in args.cities or list(store.sources):
        try:
            result = store.update_now(source_id)
            print(f"{source_id}: {result['format']} {result['width']}×{result['height']}", flush=True)
        except Exception as error:
            failures.append({'id': source_id, 'error': str(error)})
            print(f'{source_id}: FAILED {error}', flush=True)
    maps = [record for source_id in store.sources if (record := store._metadata(source_id))]
    atomic_json(args.output / 'index.json', {'version': 1, 'downloaded_at': now(), 'maps': maps, 'failures': failures})
    store.close()
    print(f'已保存 {len(maps)} 个城市，失败 {len(failures)} 个。', flush=True)
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
