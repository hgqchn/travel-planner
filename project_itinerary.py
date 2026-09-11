"""Read-only project-wide itinerary overview and advisory date conflicts."""
import json
import hashlib
import daily_planner


def version(rows, city_id, city_name):
    """Scope edits to the exact city itinerary without unrelated city conflicts."""
    value = [city_id, city_name, sorted((row['id'], row['version']) for row in rows)]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def overview(rows, city_names):
    cities = {}
    days = {}
    city_rows = {}
    for row in rows:
        item = json.loads(row['payload'])
        city_id, date = row['city_id'], item['date']
        city_rows.setdefault(city_id, []).append(row)
        city = cities.setdefault(city_id, {
            'city_id': city_id, 'city_name': city_names.get(city_id, city_id),
            'count': 0, 'dates': set(),
        })
        city['count'] += 1
        city['dates'].add(date)
        days.setdefault(date, []).append({
            'id': row['id'], 'city_id': city_id, 'city_name': city['city_name'],
            'start_time': '', 'time_block': daily_planner.block_label(item),
            'position': row['position'] if 'position' in row.keys() else 0, 'title': item['title'],
        })
    planned = [{**city, 'dates': sorted(city['dates'])} for city in cities.values()]
    for city in planned:
        city['version'] = version(city_rows[city['city_id']], city['city_id'], city['city_name'])
    planned.sort(key=lambda city: (city['dates'][0], city['city_name'], city['city_id']))
    # There are no end times in the data model. Same-day cross-city plans are
    # potential conflicts, including intentional transfers; never block writes.
    conflicts = [
        {'date': date, 'items': sorted(items, key=lambda item: (
            item['city_id'], item['position'], item['id']))}
        for date, items in sorted(days.items()) if len({item['city_id'] for item in items}) > 1
    ]
    return {'cities': planned, 'conflicts': conflicts}
