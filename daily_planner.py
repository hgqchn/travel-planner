"""Shared daily planning rules. Provider failures are unknown, never free travel."""
from __future__ import annotations

import copy
import datetime as dt
import math
import re

BLOCKS = [
    {"id": "morning", "label": "上午", "start": "09:00", "end": "11:00", "role": "activity"},
    {"id": "midday", "label": "午间", "start": "11:00", "end": "13:00", "role": "activity"},
    {"id": "lunch_rest", "label": "午后休息", "start": "13:00", "end": "14:00", "role": "rest"},
    {"id": "afternoon", "label": "下午", "start": "14:00", "end": "16:00", "role": "activity"},
    {"id": "flexible", "label": "机动", "start": "16:00", "end": "17:00", "role": "buffer"},
    {"id": "evening", "label": "傍晚", "start": "17:00", "end": "20:00", "role": "activity"},
    {"id": "night", "label": "夜间", "start": "20:00", "end": "22:00", "role": "activity"},
]
BLOCK_IDS = {b['id'] for b in BLOCKS}
MODES = {'transit', 'walking', 'driving', 'bicycling'}
VISIT_FIELDS = {'time_block', 'duration_minutes', 'duration_source', 'priority', 'visit_kind',
                'fixed_start', 'opening_start', 'opening_end', 'opening_source', 'opening_note', 'entry_start', 'entry_end',
                'block_locked', 'is_backup', 'poi', 'plan_version', 'legacy_start_time'}


def validate_time_block_change(previous, updated):
    if previous.get('block_locked') and updated.get('time_block', '') != previous.get('time_block', ''):
        raise ValueError('该行程已锁定时段，请先在“确定当天行程”中解除时段锁定，再修改时段。')


def minutes(value):
    if not isinstance(value, str) or not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', value):
        return None
    h, m = value.split(':')
    return int(h) * 60 + int(m)


def block_for(value):
    n = minutes(value) if isinstance(value, str) else value
    return next((b['id'] for b in BLOCKS if n is not None and minutes(b['start']) <= n < minutes(b['end'])), '')


def block_label(item):
    block = item.get('time_block', block_for(item.get('start_time', '')))
    return next((f"{b['label']} {b['start']}–{b['end']}" for b in BLOCKS if b['id'] == block), '待安排')


def integer(value, label, low=0, high=1440):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label}须为 {low}–{high} 的整数。')
    return value


def recommended_minutes(text):
    """Convert explicit place recommendations; ranges use the upper bound."""
    if not isinstance(text,str):
        return None
    text=re.sub(r'\s+','',text.lower())
    text=re.sub(r'^(建议(?:停留|游览)?|大约|约)','',text)
    text=re.sub(r'(左右|以内)$','',text)
    number=r'([0-9]+(?:\.[0-9]+)?)'
    compound=re.fullmatch(number+r'小时'+number+r'分钟',text)
    if compound:
        value=float(compound[1])*60+float(compound[2])
    else:
        match=re.fullmatch(number+r'(?:[-–—~～至到]'+number+r')?(小时|分钟|分|h)',text)
        if not match:
            return None
        value=max(float(match[1]),float(match[2] or match[1]))*(60 if match[3] in {'小时','h'} else 1)
    return math.ceil(value) if 0 < value <= 1440 else None


def poi(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {'id', 'name', 'address', 'location'}:
        raise ValueError('地点格式无效。')
    from amap_service import coordinate, AMapError
    try:
        location = coordinate(value.get('location'))
    except AMapError as exc:
        raise ValueError(exc.message) from None
    result = {'location': location}
    for field, maximum in [('id', 100), ('name', 120), ('address', 300)]:
        v = value.get(field, '')
        if not isinstance(v, str) or len(v) > maximum or (field == 'name' and not v.strip()):
            raise ValueError('请确认具体地点名称与入口。')
        result[field] = v.strip()
    return result


def normalize_visit(value):
    result = dict(value)
    legacy = result.get('legacy_start_time', result.get('start_time', ''))
    block = result.get('time_block', block_for(legacy))
    if block not in BLOCK_IDS | {''}:
        raise ValueError('请选择有效时间段。')
    duration = result.get('duration_minutes')
    if duration is not None:
        integer(duration, '停留分钟数', 1)
    source = result.get('duration_source', 'user' if duration is not None else 'unknown')
    if source not in {'user', 'supplied_data', 'ai_estimate', 'unknown'}:
        raise ValueError('停留时间来源无效。')
    kind = result.get('visit_kind', 'legacy' if len(result.get('attraction_names', [])) > 1 else 'place')
    if kind not in {'place', 'attraction', 'meal', 'rest', 'transit', 'legacy'}:
        raise ValueError('活动类型无效。')
    if len(result.get('attraction_names',[]))>1:
        kind='legacy'
    priority = result.get('priority', 'preferred')
    if priority not in {'must', 'preferred', 'optional'}:
        raise ValueError('地点优先级无效。')
    # Deprecated appointment fields do not constrain current plans.
    result.update(fixed_start='', entry_start='', entry_end='')
    for field in ('opening_start', 'opening_end'):
        v = result.get(field, '')
        if v != '' and minutes(v) is None:
            raise ValueError('开放时间格式须为 HH:MM。')
        result[field] = v
    for a, b in [('opening_start', 'opening_end')]:
        if bool(result[a]) != bool(result[b]) or (result[a] and minutes(result[a]) >= minutes(result[b])):
            raise ValueError('时间窗口须同时填写起止，且结束晚于开始。')
    hours_source = result.get('opening_source', 'user' if result['opening_start'] else 'unknown')
    if hours_source not in {'user','ai_estimate','unknown'}:
        raise ValueError('开放时间来源无效。')
    note = result.get('opening_note','')
    if not isinstance(note,str) or len(note)>500:
        raise ValueError('开放时间说明不超过 500 字。')
    result.update(opening_source=hours_source, opening_note=note.strip())
    for field in ('block_locked', 'is_backup'):
        if type(result.get(field, False)) is not bool:
            raise ValueError('锁定与备选状态须为布尔值。')
        result[field] = result.get(field, False)
    result.update(plan_version=2, time_block=block, duration_minutes=duration, duration_source=source,
                  visit_kind=kind, priority=priority, poi=poi(result.get('poi')), legacy_start_time=legacy)
    return result


def defaults():
    return {'start_time': '09:00', 'end_time': '20:00', 'night_enabled': False,
            'pace': 'balanced', 'buffer_minutes': 10, 'buffer_ratio': 0.2, 'slack_target': 60,
            'lunch_minutes': 60, 'dinner_minutes': 60, 'rest_minutes': 30,
            'start_anchor': None, 'end_anchor': None, 'leg_modes': {}, 'order_mode': 'manual',
            'before': []}


def normalize_settings(value):
    if not isinstance(value, dict) or set(value) - set(defaults()):
        raise ValueError('每日设置包含未知字段。')
    result = {**defaults(), **value}
    start, end = minutes(result['start_time']), minutes(result['end_time'])
    if start is None or end is None or start >= end:
        raise ValueError('当天结束时间须晚于开始时间。')
    if type(result['night_enabled']) is not bool:
        raise ValueError('夜游开关格式无效。')
    if not result['night_enabled'] and end > 1200:
        raise ValueError('20:00 后的安排需要开启夜游。')
    for field in ('buffer_minutes', 'slack_target', 'lunch_minutes', 'dinner_minutes', 'rest_minutes'):
        integer(result[field], field, 0, 180)
    ratio = result['buffer_ratio']
    if type(ratio) not in (int, float) or not math.isfinite(ratio) or not 0 <= ratio <= 1:
        raise ValueError('缓冲比例须为 0–1。')
    if result['pace'] not in {'relaxed', 'balanced', 'packed'} or result['order_mode'] not in {'manual', 'optimized'}:
        raise ValueError('节奏或排序模式无效。')
    for field in ('start_anchor', 'end_anchor'):
        result[field] = poi(result[field])
    import json
    if not isinstance(result['leg_modes'], dict) or len(result['leg_modes']) > 1000:
        raise ValueError('逐段交通设置无效。')
    for key, mode in result['leg_modes'].items():
        try:
            pair = json.loads(key)
        except (ValueError, TypeError):
            raise ValueError('路段引用无效。') from None
        if not isinstance(pair, list) or len(pair) != 2 or any(not isinstance(x, str) or len(x) > 100 for x in pair) or mode not in MODES:
            raise ValueError('路段引用或交通方式无效。')
    before = result['before']
    if not isinstance(before, list) or len(before) > 1000 or any(not isinstance(x, list) or len(x) != 2 or any(not isinstance(v, str) for v in x) or x[0] == x[1] for x in before):
        raise ValueError('先后约束无效。')
    return result


def edge_key(a, b):
    import json
    return json.dumps([a, b], separators=(',', ':'))


def group_rank(visit):
    """Chronological slots, preserving manual order inside each slot."""
    if visit.get('is_backup'):
        return len(BLOCKS)+1
    return next((i for i,b in enumerate(BLOCKS) if b['id']==visit.get('time_block')), -1)


def group_visits(visits):
    return sorted(visits, key=group_rank)


def evaluate(visits, settings, day, route):
    """Evaluate in saved order; route(payload) is an injected provider adapter."""
    settings = normalize_settings(settings)
    visits = [normalize_visit(v) for v in visits if not v.get('is_backup')]
    start, end = minutes(settings['start_time']), minutes(settings['end_time'])
    # Night is always schedulable; retain legacy settings to preserve saved route contexts.
    if any(v['time_block'] == 'night' for v in visits):
        end = max(end, 22 * 60)
    issues, rows, legs, occupied = [], [], [], []
    totals = dict(travel_minutes=0, buffer_minutes=0, visit_minutes=0, meal_minutes=0, rest_minutes=0)
    def issue(code, message, ref=None):
        issues.append({'code': code, 'message': message, 'visit_ref': ref})
    # Real meal visits replace automatic reservations of the same part of day.
    meals = {v['time_block'] for v in visits if v['visit_kind'] == 'meal'}
    protected = []
    for label, at, duration, kind in [('午餐', 780-settings['lunch_minutes'], settings['lunch_minutes'], 'meal'),
                                       ('午休', 780, settings['rest_minutes'], 'rest'),
                                       ('晚餐', 1140-settings['dinner_minutes'], settings['dinner_minutes'], 'meal')]:
        if (label == '午餐' and 'midday' in meals) or (label == '晚餐' and 'evening' in meals):
            continue
        a, b = max(start, at), min(end, at + duration)
        if b > a:
            protected.append((a, b))
            occupied.append({'start': a, 'end': b, 'kind': kind, 'label': label})
            totals[kind + '_minutes'] += b-a
    def fit(at, length):
        for a, b in sorted(protected):
            if at < b and at + length > a:
                at = b
        return at
    ready = start
    prior = {'id': '@start', 'poi': settings['start_anchor']}
    if not prior['poi']:
        issue('missing_start', '未设置出发地点，首段交通未计入。')
    nodes = visits + ([{'id': '@end', 'poi': settings['end_anchor'], 'duration_minutes': 0,
                        'time_block': '', 'fixed_start': '', 'visit_kind': 'anchor', 'block_locked': False,
                        'opening_start': '', 'opening_end': '', 'entry_start': '', 'entry_end': ''}]
                       if settings['end_anchor'] else [])
    if not settings['end_anchor']:
        issue('missing_end', '未设置返回地点，末段交通未计入。')
    unknown = not settings['start_anchor'] or not settings['end_anchor']
    for visit in nodes:
        ref, duration = visit['id'], visit['duration_minutes']
        if visit['visit_kind'] == 'legacy':
            issue('needs_split', '此行程包含多个地点，请先拆分并填写各自停留时间。', ref)
            unknown = True
        if duration is None:
            issue('missing_duration', '请填写建议停留分钟数。', ref)
            unknown = True
        # No imaginary POI for rest. A meal without a location remains unknown.
        stationary = visit['visit_kind'] == 'rest' and not visit.get('poi')
        if not stationary and prior is not None:
            mode = settings['leg_modes'].get(edge_key(prior['id'], ref), 'transit')
            leg = {'from_ref': prior['id'], 'to_ref': ref, 'mode': mode, 'result': None,
                   'status': 'idle', 'departure': {'minutes': ready, 'provisional': unknown}, 'error': ''}
            if prior.get('poi') and visit.get('poi'):
                try:
                    departure = dt.datetime.combine(dt.date.fromisoformat(day), dt.time()) + dt.timedelta(minutes=ready)
                    result = route({'origin': prior['poi']['location'], 'destination': visit['poi']['location'],
                                    'mode': mode, 'date': departure.date().isoformat(), 'time': departure.strftime('%H:%M')})
                    seconds = result.get('duration')
                    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
                        raise ValueError('路线没有有效耗时。')
                    travel = math.ceil(seconds / 60)
                    buffer = max(settings['buffer_minutes'], math.ceil(travel * settings['buffer_ratio']))
                    depart = fit(ready, travel + buffer)
                    # Protected meals/rest can postpone departure. Transit must use that time.
                    if depart != ready and mode == 'transit':
                        departure += dt.timedelta(minutes=depart-ready)
                        result = route({'origin': prior['poi']['location'], 'destination': visit['poi']['location'],
                                        'mode': mode, 'date': departure.date().isoformat(), 'time': departure.strftime('%H:%M')})
                        seconds = result.get('duration')
                        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
                            raise ValueError('路线没有有效耗时。')
                        travel = math.ceil(seconds / 60)
                        buffer = max(settings['buffer_minutes'], math.ceil(travel*settings['buffer_ratio']))
                        if fit(depart, travel+buffer) != depart:
                            raise ValueError('交通与休息窗口冲突，请调整后重新计算。')
                    occupied.append({'start': depart, 'end': depart+travel+buffer, 'kind': 'travel', 'label': '交通与缓冲'})
                    leg.update(result=result, status='ready', departure={'minutes': depart, 'provisional': unknown})
                    totals['travel_minutes'] += travel
                    totals['buffer_minutes'] += buffer
                    ready = depart + travel + buffer
                except Exception as exc:
                    leg.update(status='error', error=getattr(exc, 'message', '此段路线不可用，请重试。'))
                    issue('route_unavailable', leg['error'], ref)
                    unknown = True
            else:
                issue('missing_poi', '请确认该段两端的具体地点。', ref)
                unknown = True
            legs.append(leg)
        block = next((b for b in BLOCKS if b['id'] == visit['time_block']), None)
        if block:
            ready = max(ready, minutes(block['start']))
        arrival = ready
        estimated_hours = visit.get('opening_source') == 'ai_estimate'
        if estimated_hours:
            issue('opening_unverified', '开放时间由 AI 提供，仅供参考，请核实后再作为时间约束。', ref)
        earliest = ready if estimated_hours else max(ready, minutes(visit.get('opening_start')) or ready)
        begin = fit(earliest, duration or 0)
        late = 0  # Retained in the evaluation response for compatibility.
        finish = begin + (duration or 0)
        if not estimated_hours and visit.get('opening_end') and finish > minutes(visit['opening_end']):
            issue('constraint_conflict', '预计超出开放时间窗口。', ref)
        if visit.get('block_locked') and block and (begin >= minutes(block['end']) or begin < minutes(block['start'])):
            issue('constraint_conflict', '无法在已锁定时段开始。', ref)
        elif block and begin >= minutes(block['end']):
            issue('block_shifted', '当前顺序下已移至后续时段，可调整顺序或时段偏好。', ref)
        if ref != '@end' and visit['visit_kind'] not in {'rest', 'transit'} and not visit.get('opening_start'):
            issue('opening_unknown', '开放或预约情况待核实。', ref)
        if duration:
            kind = visit['visit_kind'] if visit['visit_kind'] in {'meal', 'rest'} else 'visit'
            totals[kind+'_minutes'] += duration
            occupied.append({'start': begin, 'end': finish, 'kind': kind, 'visit_ref': ref})
        rows.append({'visit_ref': ref, 'arrival': None if unknown else arrival,
                     'begin': None if unknown else begin, 'departure': None if unknown else finish,
                     'estimated_begin': begin, 'estimated_departure': finish,
                     'late': None if unknown else late, 'block_id': block_for(begin)})
        ready = finish
        if not stationary:
            prior = visit
    ids = [v['id'] for v in visits]
    for a, b in settings['before']:
        if a not in ids or b not in ids or ids.index(a) >= ids.index(b):
            issue('constraint_conflict', '当前顺序不满足锁定的先后关系。', a)
    used = sum(totals.values())
    slack = max(0, end-start-used)
    overflow = max(0, ready-end, used-(end-start))
    if overflow:
        issue('overflow', f'当前预算至少超出 {overflow} 分钟。')
    route_status = ('partial' if any(l['result'] for l in legs) else 'unavailable') if unknown else 'checked'
    constraints = 'conflict' if any(x['code'] == 'constraint_conflict' for x in issues) else (
        'needs_verification' if any(x['code'] in {'opening_unknown','opening_unverified'} for x in issues) else 'clear')
    usage = []
    for block in BLOCKS:
        a, b = max(start, minutes(block['start'])), min(end, minutes(block['end']))
        used_here = sum(max(0, min(b, x['end'])-max(a, x['start'])) for x in occupied) if b>a else 0
        usage.append({'block_id': block['id'], 'available_minutes': max(0,b-a), 'used_minutes': used_here,
                      'free_minutes': max(0,b-a-used_here)})
    return {**totals, 'slack_minutes': None if unknown else slack, 'overflow_minutes': overflow,
            'time_fit': 'overflow' if overflow else 'unknown' if unknown else 'tight' if slack < settings['slack_target'] else 'fits',
            'route_status': route_status, 'constraint_status': constraints,
            'issues': issues, 'rows': rows, 'legs': legs, 'block_usage': usage}


def candidate_orders(visits, settings, optimize=False):
    active = group_visits([v for v in visits if not v.get('is_backup')])
    orders = [active]
    if not optimize or len(active) < 2:
        return orders
    # Keep locked blocks in their positions; optimize flexible runs.
    out, pending = [], []
    origin = settings.get('start_anchor')
    def flush():
        nonlocal origin
        while pending:
            if origin and all(v.get('poi') for v in pending):
                x, y = map(float, origin['location'].split(','))
                pending.sort(key=lambda v: (sum((a-b)**2 for a,b in zip(map(float,v['poi']['location'].split(',')), (x,y))), v['id']))
            nxt = pending.pop(0)
            out.append(nxt)
            origin = nxt.get('poi')
    previous_block = None
    for v in active:
        if previous_block != v.get('time_block'):
            flush()
        previous_block = v.get('time_block')
        if v.get('block_locked'):
            flush(); out.append(v); origin = v.get('poi')
        else:
            pending.append(v)
    flush()
    if [v['id'] for v in out] != [v['id'] for v in active]:
        orders.append(copy.deepcopy(out))
    return orders
