"""One operator-selected Markdown table; never follows links or emits coordinates."""
from datetime import datetime
import math
from pathlib import Path

HEADER = ['名称', '地址', '纬度', '经度', '半径米']
MAX_NOTE_BYTES = 65536


def read_places(path):
    if not path or not Path(path).is_absolute():
        return []
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(MAX_NOTE_BYTES + 1)
        if len(raw) > MAX_NOTE_BYTES:
            return []
        lines = raw.decode('utf-8').splitlines()
        rows, found, separator = [], False, False
        for line in lines:
            if not line.strip().startswith('|'):
                if found and separator:
                    break
                continue
            cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
            if not found:
                found = cells == HEADER
                continue
            if not separator:
                if len(cells) != 5 or not all(c.strip(':').strip('-') == '' and '-' in c for c in cells):
                    return []
                separator = True
                continue
            if len(cells) != 5 or not cells[0] or len(cells[0]) > 12 or not cells[1]:
                return []
            lat, lon, radius = map(float, cells[2:])
            if not all(map(math.isfinite, (lat, lon, radius))) or not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0 < radius <= 10000):
                return []
            rows.append((cells[0], lat, lon, radius))
        if len(rows) > 100 or len({row[0] for row in rows}) != len(rows):
            return []
        return rows
    except (OSError, UnicodeError, ValueError):
        return []


def distance(a, b, c, d):
    lat, other = math.radians(a), math.radians(c)
    h = math.sin((other - lat) / 2) ** 2 + math.cos(lat) * math.cos(other) * math.sin(math.radians(d - b) / 2) ** 2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(h)))


def location_summary(point, path, now):
    empty = {'place': None, 'age_minutes': None, 'sampled_at': None}
    if point is None or point['timestamp'] > now.timestamp() + 10:
        return empty
    age = max(0, now.timestamp() - point['timestamp'])
    matches = [(distance(point['latitude'], point['longitude'], lat, lon), name, radius)
               for name, lat, lon, radius in read_places(path)]
    # The uncertainty circle must fit inside the named place; imprecise fixes
    # must not turn a neighbourhood into a confident "home" observation.
    matches = sorted((meters, name) for meters, name, radius in matches if meters + point['accuracy'] <= radius)
    return {'place': matches[0][1] if matches else None, 'age_minutes': round(age / 60, 1),
            'sampled_at': datetime.fromtimestamp(point['timestamp'], now.tzinfo).isoformat()}
