"""Versioned daily aggregates over existing itinerary items."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import threading
import time
import uuid

import daily_planner as planner

_candidates = {}
_lock = threading.Lock()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def ensure_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS day_routes (
        city_id TEXT NOT NULL, date TEXT NOT NULL, edge TEXT NOT NULL,
        context TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(city_id,date,edge))''')
    db.execute('''CREATE TABLE IF NOT EXISTS day_plans (
        city_id TEXT NOT NULL REFERENCES cities(id) ON DELETE CASCADE,
        date TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, payload TEXT NOT NULL,
        PRIMARY KEY(city_id,date))''')
    db.execute('''CREATE TABLE IF NOT EXISTS daily_plan_history (
        item_id TEXT PRIMARY KEY, payload TEXT NOT NULL, position INTEGER NOT NULL)''')
    db.execute('CREATE TABLE IF NOT EXISTS day_evaluations (city_id TEXT NOT NULL, date TEXT NOT NULL, version TEXT NOT NULL, evaluated_at REAL NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(city_id,date))')
    db.execute('CREATE TABLE IF NOT EXISTS daily_apply_receipts (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, result TEXT NOT NULL)')
    db.execute('''CREATE TABLE IF NOT EXISTS day_plan_epochs (
        city_id TEXT NOT NULL REFERENCES cities(id) ON DELETE CASCADE,
        date TEXT NOT NULL, epoch INTEGER NOT NULL, PRIMARY KEY(city_id,date))''')
    if db.execute("SELECT 1 FROM meta WHERE key='daily_plan_v2'").fetchone():
        return
    rows = db.execute("SELECT * FROM items WHERE kind='itinerary'").fetchall()
    groups = {}
    for row in rows:
        data = json.loads(row['payload'])
        groups.setdefault((row['city_id'], data['date']), []).append((row, data))
    for values in groups.values():
        values.sort(key=lambda v: (v[1].get('start_time') or '99:99', v[0]['position'], v[0]['id']))
        for index, (row, data) in enumerate(values):
            db.execute('INSERT OR IGNORE INTO daily_plan_history VALUES(?,?,?)', (row['id'], row['payload'], row['position']))
            db.execute('UPDATE items SET payload=?,position=?,version=version+1 WHERE id=?',
                       (dumps(planner.normalize_visit(data)), index+1, row['id']))
    if rows:
        db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")
    db.execute("INSERT INTO meta(key,value) VALUES('daily_plan_v2',1)")


def read(db, city_id, date):
    if not isinstance(city_id, str) or not city_id or not isinstance(date, str):
        raise ValueError('请指定城市与日期。')
    if dt.date.fromisoformat(date).isoformat() != date:
        raise ValueError('日期须为 YYYY-MM-DD。')
    city = db.execute('SELECT name FROM cities WHERE id=?', (city_id,)).fetchone()
    if not city:
        raise ValueError('当前项目中没有该城市。')
    row = db.execute('SELECT * FROM day_plans WHERE city_id=? AND date=?', (city_id,date)).fetchone()
    settings = planner.normalize_settings(json.loads(row['payload'])) if row else planner.defaults()
    visits = []
    for item in db.execute("SELECT * FROM items WHERE city_id=? AND kind='itinerary' ORDER BY position,id", (city_id,)):
        payload = json.loads(item['payload'])
        if payload.get('date') == date:
            visits.append({**planner.normalize_visit(payload), 'id': item['id'], 'position': item['position'],
                           'version': item['version'], 'city_id': city_id, 'kind': 'itinerary'})
    visits = planner.group_visits(visits)
    for index, visit in enumerate(visits, 1):
        visit['position'] = index
    version_data = [city_id,city['name'],date,settings,row['version'] if row else 0,
                    [(v['id'],v['version'],v['position']) for v in visits]]
    epoch = db.execute('SELECT epoch FROM day_plan_epochs WHERE city_id=? AND date=?', (city_id,date)).fetchone()
    if epoch:
        version_data.append(epoch['epoch'])
    version = hashlib.sha256(dumps(version_data).encode()).hexdigest()
    evaluation_row=db.execute('SELECT * FROM day_evaluations WHERE city_id=? AND date=?',(city_id,date)).fetchone()
    evaluation=None
    if evaluation_row:
        evaluation=json.loads(evaluation_row['payload'])
        evaluation['evaluated_at']=evaluation_row['evaluated_at']
        if evaluation_row['version']!=version:
            evaluation.pop('legs',None)
        if evaluation_row['version']!=version or time.time()-evaluation_row['evaluated_at']>900:
            evaluation['route_status']='stale'
            evaluation['time_fit']='unknown'
            evaluation['slack_minutes']=None
    plan = {'city_id': city_id, 'date': date, 'version': version, 'settings': settings,
            'visits': visits, 'blocks': planner.BLOCKS, 'evaluation':evaluation}
    plan['routes'] = []
    for saved in db.execute('SELECT * FROM day_routes WHERE city_id=? AND date=?', (city_id,date)):
        leg = json.loads(saved['payload'])
        if saved['context'] == route_context(plan, leg['from_ref'], leg['to_ref']):
            plan['routes'].append(leg)
    if evaluation and evaluation.get('legs'):
        existing = {(l['from_ref'],l['to_ref']) for l in plan['routes']}
        plan['routes'].extend(dict(l,source='evaluation',saved_at=evaluation['evaluated_at']) for l in evaluation['legs']
                              if l.get('result') and (l['from_ref'],l['to_ref']) not in existing)
    return plan


def route_nodes(plan):
    nodes = [v for v in plan['visits'] if not v['is_backup']]
    for key, ref, first in [('start_anchor','@start',True),('end_anchor','@end',False)]:
        if plan['settings'][key]:
            anchor = dict(id=ref, poi=plan['settings'][key], duration_minutes=0, visit_kind='anchor', time_block='')
            nodes.insert(0, anchor) if first else nodes.append(anchor)
    return nodes


def route_context(plan, from_ref, to_ref):
    nodes = route_nodes(plan)
    moving = [v for v in nodes if not (v['visit_kind']=='rest' and not v.get('poi'))]
    pairs = [(a['id'], b['id']) for a,b in zip(moving,moving[1:])]
    if (from_ref,to_ref) not in pairs:
        return None
    edge = planner.edge_key(from_ref,to_ref)
    mode = plan['settings']['leg_modes'].get(edge,'transit')
    end = next(i for i,v in enumerate(nodes) if v['id']==to_ref)
    relevant = nodes[:end+1] if mode=='transit' else [v for v in nodes if v['id'] in (from_ref,to_ref)]
    fields = ('id','poi','duration_minutes','time_block','visit_kind','opening_start','opening_end','opening_source','block_locked') if mode=='transit' else ('id','poi')
    settings = {k:v for k,v in plan['settings'].items() if k not in ('leg_modes','order_mode')} if mode=='transit' else {}
    if mode=='transit':
        settings['meal_blocks'] = [v['time_block'] for v in nodes if v['visit_kind']=='meal']
    modes = [plan['settings']['leg_modes'].get(planner.edge_key(a,b),'transit') for a,b in pairs[:pairs.index((from_ref,to_ref))+1]] if mode=='transit' else [mode]
    return dumps([plan['date'], settings, modes, [{k:v.get(k) for k in fields} for v in relevant]])


def save_route(s, db_path, data, user_id, amap):
    if not isinstance(data,dict) or set(data)-{'city_id','date','version','from_ref','to_ref','departure'}:
        raise s.ApiError(400,'路段请求格式无效。')
    plan = get(s,db_path,data.get('city_id'),data.get('date'))
    if data.get('version') != plan['version']:
        raise s.ApiError(409,'当天安排已变化，请载入最新版后规划。')
    a,b = data.get('from_ref'),data.get('to_ref')
    context = route_context(plan,a,b)
    if context is None:
        raise s.ApiError(400,'请选择当前日计划的相邻路段。')
    nodes = route_nodes(plan)
    source,target = [next(v for v in nodes if v['id']==ref) for ref in (a,b)]
    if not source.get('poi') or not target.get('poi'):
        raise s.ApiError(400,'请先确认两端地点。')
    departure = data.get('departure')
    if not isinstance(departure,dict) or type(departure.get('minutes')) is not int or not 0<=departure['minutes']<=10080:
        raise s.ApiError(400,'出发时间无效。')
    departure = {k:departure[k] for k in ('minutes','provisional','context') if k in departure}
    edge = planner.edge_key(a,b)
    mode = plan['settings']['leg_modes'].get(edge,'transit')
    when = dt.datetime.combine(dt.date.fromisoformat(plan['date']),dt.time())+dt.timedelta(minutes=departure['minutes'])
    with s.connect_db(db_path) as db:
        city = db.execute('SELECT name FROM cities WHERE id=?',(plan['city_id'],)).fetchone()['name']
    result = amap.route(dict(origin=source['poi']['location'],destination=target['poi']['location'],mode=mode,
                             date=when.date().isoformat(),time=when.strftime('%H:%M')),city)
    leg = dict(from_ref=a,to_ref=b,mode=mode,result=result,status='ready',error='',departure=departure,
               source='reference',saved_at=time.time())
    with s.connect_db(db_path) as db:
        db.execute('BEGIN IMMEDIATE')
        current = read(db,plan['city_id'],plan['date'])
        if current['version']!=plan['version'] or dumps(current['routes'])!=dumps(plan['routes']):
            raise s.ApiError(409,'规划期间当天安排已变化，请重新规划。')
        # A changed earlier duration changes subsequent transit departures.
        later = {v['id'] for v in nodes[nodes.index(target):]}
        previous = next((l for l in plan['routes'] if (l['from_ref'],l['to_ref'])==(a,b)),None)
        duration_changed = not previous or previous['result']['duration'] != result['duration']
        for row in db.execute('SELECT * FROM day_routes WHERE city_id=? AND date=?',(plan['city_id'],plan['date'])).fetchall():
            old = json.loads(row['payload'])
            if duration_changed and old['mode']=='transit' and old['from_ref'] in later:
                db.execute('DELETE FROM day_routes WHERE city_id=? AND date=? AND edge=?',(plan['city_id'],plan['date'],row['edge']))
        db.execute('INSERT OR REPLACE INTO day_routes VALUES(?,?,?,?,?)',(plan['city_id'],plan['date'],edge,context,dumps(leg)))
        db.execute('DELETE FROM day_evaluations WHERE city_id=? AND date=?',(plan['city_id'],plan['date']))
        s.next_revision(db)
    return leg


def get(s, db_path, city_id, date):
    try:
        with s.connect_db(db_path) as db:
            db.execute('BEGIN')
            return read(db, city_id, date)
    except ValueError as exc:
        raise s.ApiError(400, str(exc)) from None


def change_day(s, db_path, data, user_id, *, delete=False):
    """Create an empty day or delete its entire aggregate in one transaction."""
    fields = {'city_id', 'date', 'version'} if delete else {'city_id', 'date'}
    if not isinstance(data, dict) or set(data) != fields:
        raise s.ApiError(400, '请提供城市、日期' + ('和当天版本。' if delete else '。'))
    try:
        with s.connect_db(db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM users WHERE user_id=?', (user_id,)).fetchone():
                raise s.ApiError(401, '请先选择用户 ID。')
            plan = read(db, data['city_id'], data['date'])
            city_id, date = plan['city_id'], plan['date']
            exists = bool(plan['visits']) or db.execute(
                'SELECT 1 FROM day_plans WHERE city_id=? AND date=?', (city_id,date)).fetchone()
            if delete:
                if data['version'] != plan['version']:
                    raise s.ApiError(409, '当天安排已变化，请刷新核对后重新确认删除。')
                if not exists:
                    raise s.ApiError(404, '这一天已删除，请刷新行程。')
                for visit in plan['visits']:
                    db.execute("DELETE FROM items WHERE id=? AND city_id=? AND kind='itinerary'", (visit['id'],city_id))
                db.execute('DELETE FROM day_plans WHERE city_id=? AND date=?', (city_id,date))
                db.execute('DELETE FROM day_evaluations WHERE city_id=? AND date=?', (city_id,date))
                db.execute('DELETE FROM day_routes WHERE city_id=? AND date=?', (city_id,date))
                # Keep stale settings/AI drafts invalid even if this date is recreated.
                db.execute('''INSERT INTO day_plan_epochs VALUES(?,?,1)
                    ON CONFLICT(city_id,date) DO UPDATE SET epoch=day_plan_epochs.epoch+1''', (city_id,date))
            else:
                if exists:
                    raise s.ApiError(409, '这一天已存在，可直接打开“地点与每日设置”。')
                db.execute('INSERT INTO day_plans VALUES(?,?,1,?)', (city_id,date,dumps(planner.defaults())))
            revision = s.next_revision(db)
            db.execute('''INSERT INTO activity(revision,action,item_id,city_id,kind,item_name,user_id,happened_at)
                VALUES(?,?,?,?,?,?,?,?)''', (revision, 'delete' if delete else 'create',
                f'day:{city_id}:{date}', city_id, 'itinerary',
                f"{date} 每日计划" + (f"（含 {len(plan['visits'])} 项安排）" if delete else ''), user_id, s.utc_now()))
            return {'city_id':city_id, 'date':date, 'revision':revision,
                    'deleted_count':len(plan['visits'])} if delete else read(db,city_id,date)
    except (ValueError, TypeError) as exc:
        raise s.ApiError(400, str(exc)) from None


def save(s, db_path, data, user_id, receipt=None):
    if not isinstance(data, dict) or set(data) - {'city_id','date','version','settings','order','updates','additions','split'}:
        raise s.ApiError(400, '每日计划请求格式无效。')
    try:
        with s.connect_db(db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            if receipt:
                prior = db.execute('SELECT * FROM daily_apply_receipts WHERE id=?',(receipt,)).fetchone()
                if prior:
                    if prior['user_id'] != user_id:
                        raise s.ApiError(403,'无法访问此应用回执。')
                    return json.loads(prior['result'])
            plan = read(db,data.get('city_id'),data.get('date'))
            if data.get('version') != plan['version']:
                raise s.ApiError(409,'当天安排已被修改，请载入最新版本；本地草稿保留。',{'current':plan})
            city_id, date = plan['city_id'], plan['date']
            settings = planner.normalize_settings({**plan['settings'], **data.get('settings', {})})
            ids = [v['id'] for v in plan['visits']]
            order = data.get('order',ids)
            if not isinstance(order,list) or any(not isinstance(x,str) for x in order) or len(order)!=len(ids) or set(order)!=set(ids):
                raise ValueError('排序必须包含当天每个活动且恰好一次。')
            updates = data.get('updates', [])
            if not isinstance(updates,list):
                raise ValueError('活动修改列表无效。')
            payloads = {v['id']: {k:v[k] for k in set(s.FIELD_RULES['itinerary']) | planner.VISIT_FIELDS | {'attraction_names'} if k in v} for v in plan['visits']}
            seen = set()
            for update in updates:
                if not isinstance(update,dict) or set(update)!={'id','changes'} or not isinstance(update['id'],str) or update['id'] not in payloads or update['id'] in seen:
                    raise ValueError('修改的活动引用无效或重复。')
                seen.add(update['id'])
                changes = update['changes']
                allowed = planner.VISIT_FIELDS | {'title','location','category','notes','link','attraction_names'}
                if not isinstance(changes,dict) or set(changes)-allowed:
                    raise ValueError('活动修改字段无效。')
                merged = {**payloads[update['id']],**changes}
                planner.validate_time_block_change(payloads[update['id']], merged)
                if any(k in changes and changes[k] != payloads[update['id']].get(k) for k in ('location','attraction_names')) and 'poi' not in changes:
                    merged['poi'] = None
                payloads[update['id']] = s.validate_payload('itinerary',merged)
            additions = data.get('additions',[])
            if not isinstance(additions,list) or any(not isinstance(x,dict) for x in additions):
                raise ValueError('新增地点格式无效。')
            split = data.get('split')
            if split is not None:
                if split not in payloads or payloads[split]['visit_kind'] != 'legacy':
                    raise ValueError('只能拆分包含多个地点的旧活动。')
                old = payloads[split]
                names = old['attraction_names']
                if len(names)<2:
                    raise ValueError('请先明确该活动的多个地点。')
                payloads[split] = {**old,'title':names[0],'location':names[0],'attraction_names':[names[0]],
                                   'visit_kind':'attraction','duration_minutes':None,'duration_source':'unknown','poi':None}
                additions = [{**old,'title':name,'location':name,'attraction_names':[name],
                              'visit_kind':'attraction','duration_minutes':None,'duration_source':'unknown',
                              'fixed_start':'','poi':None} for name in names[1:]] + additions
            city = db.execute('SELECT name FROM cities WHERE id=?',(city_id,)).fetchone()['name']
            now = s.utc_now()
            new_ids = []
            for raw in additions:
                if raw.get('date',date)!=date:
                    raise ValueError('只能向当前日期添加活动。')
                s.ensure_item_capacity(db,city_id,'itinerary')
                if 'source_place_id' in raw:
                    if not isinstance(raw['source_place_id'],str):
                        raise ValueError('地点清单引用无效。')
                    source=db.execute("SELECT payload FROM items WHERE id=? AND city_id=? AND kind='attraction'",(raw['source_place_id'],city_id)).fetchone()
                    if not source:
                        raise s.ApiError(409,'地点已变化，请重新从清单选择。')
                    place=json.loads(source['payload'])
                    if raw.get('title')!=place['name'] or raw.get('attraction_names')!=[place['name']]:
                        raise s.ApiError(409,'地点名称已变化，请重新从清单选择。')
                    recommended=planner.recommended_minutes(place.get('duration'))
                    if raw.get('duration_minutes') is None and recommended is not None:
                        raw={**raw,'duration_minutes':recommended,'duration_source':'supplied_data'}
                payload = s.validate_payload('itinerary',{**raw,'date':date})
                ref = uuid.uuid4().hex[:12]
                db.execute('INSERT INTO items VALUES(?,?,?, ?,?,1,?,?,?,?)',
                           (ref,city_id,'itinerary',len(ids)+len(new_ids)+1,dumps(payload),now,now,user_id,user_id))
                payloads[ref] = payload
                new_ids.append(ref)
            if split and new_ids:
                offset = order.index(split)+1
                order = order[:offset]+new_ids+order[offset:]
            else:
                order = order + new_ids
            order.sort(key=lambda ref: planner.group_rank(payloads[ref]))
            # Modes are bound to directed visit pairs; obsolete edges are discarded.
            active = [ref for ref in order if not payloads[ref]['is_backup'] and not (payloads[ref]['visit_kind']=='rest' and not payloads[ref]['poi'])]
            nodes = (['@start'] if settings['start_anchor'] else [])+active+(['@end'] if settings['end_anchor'] else [])
            edges = {planner.edge_key(a,b) for a,b in zip(nodes,nodes[1:])}
            settings['leg_modes'] = {k:v for k,v in settings['leg_modes'].items() if k in edges}
            if any(a not in order or b not in order for a,b in settings['before']):
                raise ValueError('先后约束包含不属于当天的活动。')
            if 'order' in data and 'order_mode' not in data.get('settings',{}):
                settings['order_mode']='manual'
            for index,ref in enumerate(order):
                db.execute('UPDATE items SET payload=?,position=?,version=version+1,updated_at=?,updated_by=? WHERE id=?',
                           (dumps(payloads[ref]),index+1,now,user_id,ref))
                revision = s.next_revision(db)
                db.execute("INSERT INTO activity(revision,action,item_id,city_id,kind,item_name,user_id,happened_at) VALUES(?,?,?,?,?,?,?,?)",
                           (revision,'create' if ref in new_ids else 'update',ref,city_id,'itinerary',payloads[ref]['title'],user_id,now))
                row = db.execute('SELECT * FROM items WHERE id=?',(ref,)).fetchone()
                s.sync_itinerary_attractions(db,row,city,user_id,now)
            db.execute('INSERT INTO day_plans VALUES(?,?,1,?) ON CONFLICT(city_id,date) DO UPDATE SET payload=excluded.payload,version=day_plans.version+1',
                       (city_id,date,dumps(settings)))
            s.next_revision(db)
            result = read(db,city_id,date)
            if receipt:
                db.execute('INSERT INTO daily_apply_receipts VALUES(?,?,?)',(receipt,user_id,dumps(result)))
        return result
    except (ValueError,TypeError) as exc:
        raise s.ApiError(400,str(exc)) from None


def evaluation_summary(evaluation):
    summary = dict(evaluation)
    summary['leg_summaries'] = [dict(from_ref=l['from_ref'],to_ref=l['to_ref'],mode=l['mode'],
        duration_minutes=__import__('math').ceil(l['result']['duration']/60) if l['result'] else None,
        status=l['status']) for l in evaluation['legs']]
    return summary


def persist_evaluated_routes(db, plan, evaluation):
    db.execute('DELETE FROM day_routes WHERE city_id=? AND date=?',(plan['city_id'],plan['date']))
    for leg in evaluation['legs']:
        context=route_context(plan,leg['from_ref'],leg['to_ref'])
        if leg.get('result') and context:
            saved=dict(leg,source='evaluation',saved_at=time.time())
            db.execute('INSERT OR REPLACE INTO day_routes VALUES(?,?,?,?,?)',
                (plan['city_id'],plan['date'],planner.edge_key(leg['from_ref'],leg['to_ref']),context,dumps(saved)))


def evaluate(s, db_path, data, user_id, amap):
    if not isinstance(data,dict) or type(data.get('optimize',False)) is not bool:
        raise s.ApiError(400,'路线评估请求无效。')
    plan = get(s,db_path,data.get('city_id'),data.get('date'))
    if data.get('version')!=plan['version']:
        raise s.ApiError(409,'当天安排已变化，请重新计算。')
    with s.connect_db(db_path) as db:
        city = db.execute('SELECT name FROM cities WHERE id=?',(plan['city_id'],)).fetchone()['name']
    # Per-request reuse only. Transit departure context is part of the key.
    cache = {}
    def route(payload):
        key = dumps(payload)
        if key not in cache:
            cache[key] = amap.route(payload,city)
        return cache[key]
    candidates = []
    for order in planner.candidate_orders(plan['visits'],plan['settings'],data.get('optimize',False)):
        evaluation = planner.evaluate(order,plan['settings'],plan['date'],route)
        candidates.append({'candidate_ref': uuid.uuid4().hex, 'order':[v['id'] for v in order], 'evaluation':evaluation})
    current = get(s,db_path,plan['city_id'],plan['date'])
    if current['version']!=plan['version']:
        raise s.ApiError(409,'计算期间当天安排已变化，已丢弃旧结果。')
    # Keep the saved-order geometry for reopening and offline exports.
    summary=evaluation_summary(candidates[0]['evaluation'])
    with s.connect_db(db_path) as db:
        db.execute('BEGIN IMMEDIATE')
        if read(db,plan['city_id'],plan['date'])['version']!=plan['version']:
            raise s.ApiError(409,'计划已变化，请重新核算。')
        db.execute('INSERT OR REPLACE INTO day_evaluations VALUES(?,?,?,?,?)',
                   (plan['city_id'],plan['date'],plan['version'],time.time(),dumps(summary)))
        persist_evaluated_routes(db,plan,summary)
        s.next_revision(db)
    with _lock:
        now = time.monotonic()
        for key in list(_candidates):
            if now-_candidates[key]['created']>900:
                del _candidates[key]
        while len(_candidates)>126:
            del _candidates[next(iter(_candidates))]
        for candidate in candidates:
            _candidates[candidate['candidate_ref']] = {'created':now,'db':str(db_path),'user':user_id,
                                                       'plan':plan,'candidate':candidate}
    return {'city_id':plan['city_id'],'date':plan['date'],'version':plan['version'],'candidates':candidates}


def candidate(s, db_path, user_id, ref):
    with _lock:
        record = _candidates.get(ref) if isinstance(ref,str) else None
        if not record or record['db']!=str(db_path) or record['user']!=user_id or time.monotonic()-record['created']>900:
            raise s.ApiError(409,'候选已过期，请重新核算。')
        return record


def apply(s, db_path, data, user_id):
    if not isinstance(data,dict):
        raise s.ApiError(400,'应用请求格式无效。')
    record = candidate(s,db_path,user_id,data.get('candidate_ref'))
    old = record['plan']
    if any(data.get(k)!=old[k] for k in ('city_id','date','version')):
        raise s.ApiError(409,'候选不匹配当前日期或版本。')
    ids = record['candidate']['order']
    remainder = [v['id'] for v in old['visits'] if v['is_backup']]
    ev = record['candidate']['evaluation']
    if ev['constraint_status']=='conflict':
        raise s.ApiError(400,'候选与锁定条件冲突，请先调整约束。')
    saved = save(s,db_path,{'city_id':old['city_id'],'date':old['date'],'version':old['version'],
                          'order':ids+remainder,'settings':{'order_mode':'optimized'}},user_id)
    with s.connect_db(db_path) as db:
        db.execute('BEGIN IMMEDIATE')
        current = read(db,old['city_id'],old['date'])
        if current['version']==saved['version'] and [v['id'] for v in current['visits'] if not v['is_backup']]==ids:
            db.execute('INSERT OR REPLACE INTO day_evaluations VALUES(?,?,?,?,?)',
                       (old['city_id'],old['date'],saved['version'],time.time(),dumps(evaluation_summary(ev))))
            persist_evaluated_routes(db,current,ev)
            s.next_revision(db)
            return read(db,old['city_id'],old['date'])
    return saved


def shift_city(db, city_id, offset, delete=False):
    rows = db.execute('SELECT * FROM day_plans WHERE city_id=?',(city_id,)).fetchall()
    if delete or offset:
        db.execute('DELETE FROM day_routes WHERE city_id=?',(city_id,))
        db.execute('DELETE FROM day_evaluations WHERE city_id=?',(city_id,))
        db.execute('DELETE FROM day_plans WHERE city_id=?',(city_id,))
        if not delete:
            for row in rows:
                day=(dt.date.fromisoformat(row['date'])+dt.timedelta(days=offset)).isoformat()
                db.execute('INSERT INTO day_plans VALUES(?,?,?,?)',(city_id,day,row['version']+1,row['payload']))
