"""Persist imported AI travel guidance independently of expiring AI jobs."""

from __future__ import annotations

import json
import hashlib


def ensure_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS travel_guidance (
        id TEXT PRIMARY KEY, city_id TEXT NOT NULL REFERENCES cities(id) ON DELETE CASCADE,
        summary TEXT NOT NULL, notices TEXT NOT NULL, created_at TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS travel_guidance_items (
        guidance_id TEXT NOT NULL REFERENCES travel_guidance(id) ON DELETE CASCADE,
        item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        PRIMARY KEY(guidance_id, item_id))""")
    db.execute("CREATE INDEX IF NOT EXISTS travel_guidance_item_idx ON travel_guidance_items(item_id)")
    if 'citywide' not in {row['name'] for row in db.execute('PRAGMA table_info(travel_guidance)')}:
        db.execute('ALTER TABLE travel_guidance ADD COLUMN citywide INTEGER NOT NULL DEFAULT 1')
        # Existing food-only and single-day suggestions are not city itinerary notes.
        db.execute('''UPDATE travel_guidance SET citywide=0 WHERE NOT EXISTS (
            SELECT 1 FROM travel_guidance_items l JOIN items i ON i.id=l.item_id
            WHERE l.guidance_id=travel_guidance.id AND i.kind='itinerary')''')
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='ai_jobs'").fetchone():
            for row in db.execute('SELECT g.id,j.request_json FROM travel_guidance g JOIN ai_jobs j ON j.id=g.id').fetchall():
                if not _citywide_request(row):
                    db.execute('UPDATE travel_guidance SET citywide=0 WHERE id=?', (row['id'],))
    db.execute('CREATE INDEX IF NOT EXISTS travel_guidance_city_idx ON travel_guidance(city_id,citywide,created_at)')
    # A city has one user-controlled note. A tombstone keeps deleted tips from
    # returning when older jobs are recovered or new AI plans are imported.
    db.execute("""CREATE TABLE IF NOT EXISTS travel_guidance_overrides (
        city_id TEXT PRIMARY KEY REFERENCES cities(id) ON DELETE CASCADE,
        summary TEXT NOT NULL, notices TEXT NOT NULL, version INTEGER NOT NULL,
        deleted INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
        updated_by TEXT NOT NULL)""")


def _citywide_request(job):
    request = json.loads(job['request_json']) if 'request_json' in job.keys() and job['request_json'] else {}
    return request.get('planning_mode') != 'replace_day' and 'itinerary' in request.get('kinds', ['itinerary'])


def save(db, job, item_ids, created_at):
    if job is None or 'result_json' not in job.keys() or not job['result_json']:
        return False
    if not _citywide_request(job):
        return False
    result = json.loads(job['result_json'])
    summary = result.get('summary', '')
    summary = summary.strip() if isinstance(summary, str) else ''
    notices = list(dict.fromkeys(value.strip() for value in result.get('notices', [])
                                if isinstance(value, str) and value.strip()))
    ids = list(dict.fromkeys(value for value in item_ids if isinstance(value, str)))
    if not ids:
        return False
    rows = db.execute('SELECT id,kind FROM items WHERE city_id=? AND id IN ('
                      + ','.join('?' for _ in ids) + ')', [job['city_id'], *ids]).fetchall()
    rows = [row for row in rows if row['kind'] == 'itinerary']
    if not rows:
        return False
    inserted = db.execute('INSERT OR IGNORE INTO travel_guidance(id,city_id,summary,notices,created_at) VALUES(?,?,?,?,?)',
                          (job['id'], job['city_id'], summary,
                           json.dumps(notices, ensure_ascii=False), created_at)).rowcount
    if not inserted:
        return False
    db.executemany('INSERT INTO travel_guidance_items VALUES(?,?)',
                   [(job['id'], row['id']) for row in rows])
    return True


def recover_imports(db):
    """Recover surviving old receipts once, before AI job retention cleanup."""
    if db.execute("SELECT 1 FROM meta WHERE key='travel_guidance_v1'").fetchone():
        return 0
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    recovered = 0
    if {'ai_jobs', 'ai_import_batches'} <= tables:
        for row in db.execute('''SELECT j.*, b.result AS receipt, b.created_at AS imported_at
                                FROM ai_jobs j JOIN ai_import_batches b ON b.job_id=j.id
                                WHERE b.city_id=j.city_id AND b.user_id=j.user_id
                                ORDER BY b.created_at,j.rowid''').fetchall():
            recovered += save(db, row, json.loads(row['receipt']).get('item_ids', []), row['imported_at'])
    db.execute("INSERT INTO meta(key,value) VALUES('travel_guidance_v1',1)")
    return recovered


def _automatic(db, city_id=None):
    rows = db.execute('''SELECT g.*, c.name AS city_name
                         FROM travel_guidance g JOIN cities c ON c.id=g.city_id
                         WHERE g.citywide=1 AND EXISTS (
                             SELECT 1 FROM items i WHERE i.city_id=g.city_id AND i.kind='itinerary')'''
                      + ''' AND g.id=(SELECT latest.id FROM travel_guidance latest
                           WHERE latest.city_id=g.city_id AND latest.citywide=1
                           ORDER BY latest.created_at DESC, latest.rowid DESC LIMIT 1)'''
                      + (' AND g.city_id=?' if city_id is not None else '')
                      + ' ORDER BY c.position',
                      (city_id,) if city_id is not None else ()).fetchall()
    groups = {}
    for row in rows:
        if row['id'] not in groups:
            groups[row['id']] = {key: row[key] for key in ('id', 'city_id', 'city_name', 'summary', 'created_at')}
            groups[row['id']].update(notices=json.loads(row['notices']), dates=set())
        for item in db.execute("SELECT payload FROM items WHERE city_id=? AND kind='itinerary'", (row['city_id'],)):
            day = json.loads(item['payload']).get('date')
            if day:
                groups[row['id']]['dates'].add(day)
    for group in groups.values():
        group['dates'] = sorted(group['dates'])
    return list(groups.values())


def states(db, city_id=None):
    automatic = {group['city_id']: group for group in _automatic(db, city_id)}
    overrides = {row['city_id']: row for row in db.execute(
        'SELECT * FROM travel_guidance_overrides' + (' WHERE city_id=?' if city_id is not None else ''),
        (city_id,) if city_id is not None else ())}
    cities = db.execute('SELECT id,name FROM cities' + (' WHERE id=?' if city_id is not None else '')
                        + ' ORDER BY position', (city_id,) if city_id is not None else ()).fetchall()
    result = []
    for city in cities:
        group = automatic.get(city['id'], {'id': city['id'], 'city_id': city['id'],
            'city_name': city['name'], 'summary': '', 'notices': [], 'dates': []})
        group = dict(group, manual=False, deleted=False)
        override = overrides.get(city['id'])
        if override:
            group.update(id=city['id'], summary=override['summary'], notices=json.loads(override['notices']),
                dates=[], manual=True, deleted=bool(override['deleted']),
                updated_at=override['updated_at'], updated_by=override['updated_by'])
            # Unrelated AI imports must not invalidate or overwrite a manual note.
            group.pop('created_at', None)
            token = ['manual', city['id'], override['version']]
        else:
            token = group
        group['version'] = hashlib.sha256(json.dumps(token, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        group['summary'] = ''  # Planning narration is retained only in historical AI records.
        result.append(group)
    return result


def read(db, city_id=None):
    return [group for group in states(db, city_id)
            if not group['deleted'] and group['notices']]


def write(db, city_id, summary, notices, user_id, updated_at, *, deleted=False):
    db.execute('''INSERT INTO travel_guidance_overrides
        (city_id,summary,notices,version,deleted,updated_at,updated_by) VALUES(?,?,?,1,?,?,?)
        ON CONFLICT(city_id) DO UPDATE SET summary=excluded.summary,notices=excluded.notices,
        version=travel_guidance_overrides.version+1,deleted=excluded.deleted,
        updated_at=excluded.updated_at,updated_by=excluded.updated_by''',
        (city_id, summary, json.dumps(notices, ensure_ascii=False), int(deleted), updated_at, user_id))
    return states(db, city_id)[0]
