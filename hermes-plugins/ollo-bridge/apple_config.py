"""Non-secret Apple names and collection scopes, shared with standalone cron."""
from unicodedata import normalize
from pathlib import Path

LISTS = ('收件箱', '工作', '生活', '家庭', '等待', '长期')
CALENDARS = ('工作', '生活', '家庭')


def knowledge(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict) or value.keys() - {'places'}:
        raise ValueError('invalid_knowledge_config')
    path = value.get('places')
    if path is not None and (not isinstance(path, str) or not Path(path).is_absolute()):
        raise ValueError('invalid_places_path')
    return {'places': path}


def names(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict) or value.keys() - {'lists', 'calendars'}:
        raise ValueError('invalid_apple_config')
    result = {}
    for key, logical in (('lists', LISTS), ('calendars', CALENDARS)):
        overrides = value.get(key, {})
        if not isinstance(overrides, dict) or overrides.keys() - set(logical):
            raise ValueError('invalid_apple_names')
        mapping = {name: overrides.get(name, name) for name in logical}
        if any(not isinstance(v, str) or not v.strip() or len(v) > 128 or '\x00' in v for v in mapping.values()):
            raise ValueError('invalid_apple_name')
        # Swift String/EventKit compare canonically equivalent Unicode equally.
        if len({normalize('NFC', v) for v in mapping.values()}) != len(mapping):
            raise ValueError('duplicate_apple_name')
        result[key] = mapping
    return result


def scopes(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict) or value.keys() - {'agenda', 'inbox', 'review'}:
        raise ValueError('invalid_collection_config')
    result = {}
    for kind in ('agenda', 'inbox', 'review'):
        entry = value.get(kind, {})
        if not isinstance(entry, dict) or entry.keys() - {'lists', 'calendars'}:
            raise ValueError('invalid_collection_scope')
        scope = {}
        for key, allowed in (('lists', LISTS), ('calendars', CALENDARS)):
            default = (['收件箱'] if key == 'lists' else []) if kind == 'inbox' else list(allowed)
            selected = entry.get(key, default)
            if key == 'calendars' and selected == 'all':
                scope[key] = 'all'
                continue
            if (not isinstance(selected, list) or any(not isinstance(v, str) or v not in allowed for v in selected)
                    or len(set(selected)) != len(selected) or (key == 'lists' and not selected)):
                raise ValueError('invalid_collection_scope')
            scope[key] = list(selected)
        # Older installations explicitly stored the three destination names.
        # They remain write destinations; v2 event reads are never narrowed by them.
        if kind in {'agenda', 'review'}:
            scope['calendars'] = 'all'
        result[kind] = scope
    return result
