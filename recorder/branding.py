"""Public presentation state, separate from protected application settings."""
import json
import re
from aiohttp import web

DEFAULT = {
    'name': 'Stream Recorder',
    'dark': {'accent': '#c5c5c5', 'background': '#141414',
             'surface': '#242424', 'text': '#f5f5f5'},
    'light': {'accent': '#444444', 'background': '#f5f5f5',
              'surface': '#ffffff', 'text': '#171717'},
}
COLORS = {'accent', 'background', 'surface', 'text'}
HEX = re.compile(r'#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\Z')
RGB = re.compile(r'rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)\Z', re.I)


def color(value):
    if not isinstance(value, str):
        raise ValueError('Color must be a hex or RGB value')
    if HEX.fullmatch(value):
        value = value.lower()
        return '#' + ''.join(x * 2 for x in value[1:]) if len(value) == 4 else value
    match = RGB.fullmatch(value)
    if match and all(int(part) <= 255 for part in match.groups()):
        return '#' + ''.join(f'{int(part):02x}' for part in match.groups())
    raise ValueError('Color must be #RGB, #RRGGBB, or rgb(0–255, 0–255, 0–255)')


def public(settings):
    saved = settings.get('branding')
    result = {'name': DEFAULT['name'], 'dark': DEFAULT['dark'].copy(), 'light': DEFAULT['light'].copy()}
    if isinstance(saved, dict):
        if isinstance(saved.get('name'), str):
            result['name'] = saved['name']
        for theme in ('dark', 'light'):
            if isinstance(saved.get(theme), dict):
                for key in COLORS:
                    if key in saved[theme]:
                        result[theme][key] = saved[theme][key]
    return result


def update(current, changes):
    if not isinstance(changes, dict) or changes.keys() - {'name', 'dark', 'light'}:
        raise ValueError('Unknown branding field')
    result = public({'branding': current})
    if 'name' in changes:
        name = changes['name']
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100 or any(ord(c) < 32 or c in '<>' for c in name):
            raise ValueError('Name must be 1–100 plain-text characters')
        result['name'] = name.strip()
    for theme in ('dark', 'light'):
        if theme in changes:
            values = changes[theme]
            if not isinstance(values, dict) or values.keys() - COLORS:
                raise ValueError('Unknown color field')
            result[theme].update({field: color(value) for field, value in values.items()})
    return result


def install(app, service):
    async def branding(request):
        if request.method == 'GET':
            response = web.json_response(public(service.settings))
            response.headers['Cache-Control'] = 'no-store'
            return response
        if request.get('role') != 'admin':
            raise web.HTTPForbidden(text='Administrator permission required')
        if request.path.endswith('/reset'):
            result = public({})
        else:
            try:
                result = update(public(service.settings), await request.json())
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                raise web.HTTPBadRequest(text=str(exc)) from None
        async with service.gate:
            service.settings['branding'] = result
            service.db.execute('INSERT OR REPLACE INTO settings VALUES(1,?)', (json.dumps(service.settings),))
            service.db.commit()
        return web.json_response(result)

    # Concrete routes precede the general compatibility router.
    for prefix in ('/api/v1/branding', '/api/branding'):
        app.router.add_get(prefix, branding)
        app.router.add_put(prefix, branding)
        app.router.add_post(prefix + '/reset', branding)
