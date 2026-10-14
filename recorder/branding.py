"""Public presentation state, separate from protected application settings."""
import json
import re
import hashlib
import io
import os
import tempfile
from PIL import Image, UnidentifiedImageError
from aiohttp import web

DEFAULT = {
    'name': 'Stream Recorder',
    'dark': {'accent': '#c5c5c5', 'background': '#141414',
             'surface': '#242424', 'text': '#f5f5f5'},
    'light': {'accent': '#444444', 'background': '#f5f5f5',
              'surface': '#ffffff', 'text': '#171717'},
}
COLORS = {'accent', 'background', 'surface', 'text'}
ASSETS = {'light_logo', 'dark_logo', 'favicon'}
MIME = {'image/png': 'PNG', 'image/jpeg': 'JPEG', 'image/webp': 'WEBP'}
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
    result = {'name': DEFAULT['name'], 'dark': DEFAULT['dark'].copy(), 'light': DEFAULT['light'].copy(),
              'assets': {slot: None for slot in ASSETS}}
    if isinstance(saved, dict):
        if isinstance(saved.get('name'), str):
            result['name'] = saved['name']
        for theme in ('dark', 'light'):
            if isinstance(saved.get(theme), dict):
                for key in COLORS:
                    if key in saved[theme]:
                        result[theme][key] = saved[theme][key]
        if isinstance(saved.get('assets'), dict):
            for slot in ASSETS:
                digest = saved['assets'].get(slot)
                if isinstance(digest, str) and re.fullmatch('[a-f0-9]{64}', digest):
                    result['assets'][slot] = f'/api/v1/branding/assets/{slot}?v={digest}'
    return result


def update(current, changes):
    if not isinstance(changes, dict) or changes.keys() - {'name', 'dark', 'light'}:
        raise ValueError('Unknown branding field')
    result = public({'branding': current})
    # Asset URLs are read-only; asset mutations use the binary asset endpoints.
    result['assets'] = current.get('assets', result['assets']).copy()
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


def asset_digest(settings, slot):
    saved = settings.get('branding', {}).get('assets', {}).get(slot)
    if isinstance(saved, str) and re.fullmatch('[a-f0-9]{64}', saved):
        return saved
    return None


def canonical_image(data, content_type, slot):
    expected = MIME.get(content_type)
    if not expected:
        raise web.HTTPBadRequest(text='Supported images: PNG, JPEG, or WebP; SVG and active content are not supported')
    if expected == 'PNG' and not data.endswith(b'\x00\x00\x00\x00IEND\xaeB`\x82'):
        raise web.HTTPBadRequest(text='Invalid or trailing PNG content')
    if expected == 'JPEG' and not data.endswith(b'\xff\xd9'):
        raise web.HTTPBadRequest(text='Invalid or trailing JPEG content')
    if expected == 'WEBP' and (len(data) < 12 or data[:4] != b'RIFF' or
                                 int.from_bytes(data[4:8], 'little') + 8 != len(data)):
        raise web.HTTPBadRequest(text='Invalid or trailing WebP content')
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != expected or image.width < 1 or image.height < 1 or max(image.size) > (512 if slot == 'favicon' else 2048) or image.width * image.height > 4_000_000 or getattr(image, 'n_frames', 1) != 1:
                raise web.HTTPBadRequest(text='Invalid image format, dimensions, or animation')
            image.load()
            decoded = image.convert('RGBA' if 'A' in image.getbands() or 'transparency' in image.info else 'RGB')
            result = io.BytesIO()
            decoded.save(result, format='PNG')
            return result.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise web.HTTPBadRequest(text='Image cannot be decoded') from None


def install(app, service):
    folder = service.root / 'branding-assets'
    folder.mkdir(exist_ok=True)

    def save_settings():
        service.db.execute('INSERT OR REPLACE INTO settings VALUES(1,?)', (json.dumps(service.settings),))
        service.db.commit()

    def remove_unused():
        used = {f'{slot}-{asset_digest(service.settings, slot)}.png' for slot in ASSETS if asset_digest(service.settings, slot)}
        for file in folder.glob('*.png'):
            if file.name not in used:
                file.unlink()

    async def asset(request):
        slot = request.match_info['slot']
        if slot not in ASSETS:
            raise web.HTTPNotFound(text='Unknown branding asset')
        if request.method == 'GET':
            digest = asset_digest(service.settings, slot)
            if not digest:
                raise web.HTTPNotFound(text='Asset not configured')
            file = folder / f'{slot}-{digest}.png'
            if not file.is_file():
                raise web.HTTPNotFound(text='Asset missing')
            return web.FileResponse(file, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Content-Type': 'image/png'})
        if request.get('role') != 'admin':
            raise web.HTTPForbidden(text='Administrator permission required')
        if request.method == 'PUT':
            limit = 512 * 1024 if slot == 'favicon' else 2 * 1024 * 1024
            data = await request.content.read(limit + 1)
            if len(data) > limit:
                raise web.HTTPRequestEntityTooLarge(max_size=limit, actual_size=len(data))
            rendered = canonical_image(data, request.content_type, slot)
            digest = hashlib.sha256(rendered).hexdigest()
            target = folder / f'{slot}-{digest}.png'
            async with service.gate:
                if not target.exists():
                    fd, name = tempfile.mkstemp(dir=folder, prefix='.upload-')
                    try:
                        with os.fdopen(fd, 'wb') as output:
                            output.write(rendered)
                        os.replace(name, target)
                    finally:
                        if os.path.exists(name):
                            os.unlink(name)
                service.settings.setdefault('branding', {}).setdefault('assets', {})[slot] = digest
                save_settings()
                remove_unused()
        else:
            async with service.gate:
                service.settings.setdefault('branding', {}).setdefault('assets', {}).pop(slot, None)
                save_settings()
                remove_unused()
        return web.json_response(public(service.settings), headers={'Cache-Control':'no-store'})

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
            # Retain stored digests, not presentation URLs.
            assets = {} if request.path.endswith('/reset') else service.settings.get('branding', {}).get('assets', {}).copy()
            service.settings['branding'] = {key: value for key, value in result.items() if key != 'assets'} | {'assets': assets}
            save_settings()
            remove_unused()
        return web.json_response(public(service.settings), headers={'Cache-Control':'no-store'})

    # Concrete routes precede the general compatibility router.
    for prefix in ('/api/v1/branding', '/api/branding'):
        app.router.add_get(prefix, branding)
        app.router.add_put(prefix, branding)
        app.router.add_post(prefix + '/reset', branding)
        app.router.add_get(prefix + '/assets/{slot}', asset)
        app.router.add_put(prefix + '/assets/{slot}', asset)
        app.router.add_delete(prefix + '/assets/{slot}', asset)
