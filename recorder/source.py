"""Configured-source HTTP boundary. Credentials never leave their configured origin."""
import asyncio
import base64
import copy
import re
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import aiohttp
from aiohttp import web

MAX_RESOURCE = 64 * 1024 * 1024
MAX_PLAYLIST = 2 * 1024 * 1024

@dataclass(frozen=True)
class SourceReference:
    url: str
    config: dict
    capture: bool
    base: str

def valid_url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        parsed.port  # Validate malformed or out-of-range ports before fetching.
        return parsed.scheme in {'http', 'https'} and bool(parsed.hostname) and parsed.username is None and parsed.password is None and not parsed.fragment and not any(ord(c) < 33 for c in value)
    except ValueError:
        return False

def origin(value):
    parsed = urlsplit(value)
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)

class SourceRelay:
    def __init__(self):
        self.refs = {}
        self.preview_tokens = set()
        self.session = None
        self.capture_runner = None
        self.capture_base = None
        self.touched = {}

    def register(self, config, *, capture=False, base='/preview/'):
        if not valid_url(config['source_url']):
            raise ValueError('Source must be an HTTP(S) URL without embedded credentials')
        config = copy.deepcopy(config)
        token = self._reference(config['source_url'], config, capture, base)
        return base + token

    def _reference(self, url, config, capture, base):
        if not valid_url(url):
            raise ValueError('Unsupported source resource')
        # Reuse scoped references so live playlist refresh does not grow state.
        for token, ref in self.refs.items():
            if ref == SourceReference(url, config, capture, base):
                self.touched[token] = time.monotonic()
                return token
        now = time.monotonic()
        for old in list(self.refs):
            if now - self.touched.get(old, now) > 180:
                self.refs.pop(old, None)
                self.touched.pop(old, None)
                self.preview_tokens.discard(old)
        if len(self.refs) >= 20000:
            raise ValueError('Source resource limit exceeded')
        token = secrets.token_urlsafe(32)
        self.refs[token] = SourceReference(url, config, capture, base)
        self.touched[token] = now
        if not capture:
            self.preview_tokens.add(token)
        return token

    def invalidate_preview(self):
        for token in self.preview_tokens:
            self.refs.pop(token, None)
            self.touched.pop(token, None)
        self.preview_tokens.clear()

    async def capture_url(self, config):
        if self.capture_runner is None:
            app = web.Application(); app.router.add_get('/preview/{token}', self.handle_capture)
            self.capture_runner = web.AppRunner(app, access_log=None)
            await self.capture_runner.setup()
            site = web.TCPSite(self.capture_runner, '127.0.0.1', 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            self.capture_base = f'http://127.0.0.1:{port}/preview/'
        return self.register(config, capture=True, base=self.capture_base)

    async def close(self):
        if self.capture_runner:
            await self.capture_runner.cleanup()
        if self.session:
            await self.session.close()

    async def handle_capture(self, request):
        return await self._handle(request, True)

    async def handle(self, request):
        return await self._handle(request, False)

    async def _handle(self, request, internal):
        ref = self.refs.get(request.match_info['token'])
        if ref is None or ref.capture != internal:
            raise web.HTTPNotFound(text='Unknown or expired source reference')
        url, config, capture, base = ref.url, ref.config, ref.capture, ref.base
        self.touched[request.match_info['token']] = time.monotonic()
        try:
            if self.session is None:
                self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30, connect=10), cookie_jar=aiohttp.DummyCookieJar(), trust_env=False)
            headers = source_headers(config)
            initial_origin = origin(config['source_url'])
            forwarded = {}
            for name in ('Range',):
                if name in request.headers:
                    forwarded[name] = request.headers[name]
            for _ in range(6):
                async with self.session.get(url, headers={**forwarded, **(headers if origin(url) == initial_origin else {})}, allow_redirects=False) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        target = urljoin(url, response.headers.get('Location', ''))
                        if not valid_url(target):
                            raise ValueError('Invalid redirect')
                        url = target
                        continue
                    if response.status not in {200, 206}:
                        raise ValueError('Source unavailable')
                    data = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        data.extend(chunk)
                        if len(data) > MAX_RESOURCE:
                            raise ValueError('Source resource exceeds limit')
                    content_type = response.headers.get('Content-Type', 'application/octet-stream').split(';')[0]
                    is_playlist = bytes(data).lstrip().startswith(b'#EXTM3U')
                    if is_playlist:
                        if len(data) > MAX_PLAYLIST:
                            raise ValueError('Playlist exceeds limit')
                        text = bytes(data).decode('utf-8-sig')
                        def rewrite(value):
                            target = urljoin(url, value)
                            return base + self._reference(target, config, capture, base)
                        lines = []
                        for line in text.splitlines():
                            # Variable substitution could create unprovenanced URLs.
                            if '{$' in line or line.startswith('#EXT-X-DEFINE'):
                                raise ValueError('Playlist variables unsupported')
                            if line.startswith('#'):
                                line = re.sub(r'URI="([^"\r\n]+)"', lambda m: 'URI="' + rewrite(m[1]) + '"', line)
                            elif line.strip():
                                line = rewrite(line.strip())
                            lines.append(line)
                        return web.Response(text='\n'.join(lines) + '\n', content_type='application/vnd.apple.mpegurl', headers={'Cache-Control': 'no-store'})
                    response_headers = {'Cache-Control': 'no-store'}
                    for name in ('Content-Range', 'Accept-Ranges'):
                        if name in response.headers:
                            response_headers[name] = response.headers[name]
                    return web.Response(body=bytes(data), status=response.status, content_type=content_type, headers=response_headers)
            raise ValueError('Redirect limit exceeded')
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, UnicodeError):
            raise web.HTTPBadGateway(text='Configured source resource unavailable') from None

def source_headers(config):
    headers = dict(config.get('source_headers', {}))
    if config.get('source_basic_username'):
        value = config['source_basic_username'] + ':' + config.get('source_basic_password', '')
        headers['Authorization'] = 'Basic ' + base64.b64encode(value.encode()).decode()
    elif config.get('source_bearer_token'):
        headers['Authorization'] = 'Bearer ' + config['source_bearer_token']
    return headers

SOURCE_FIELDS = ('source_url', 'source_basic_username', 'source_basic_password', 'source_bearer_token', 'source_headers')
SECRET_FIELDS = ('source_basic_password', 'source_bearer_token', 'source_headers')

def source_config(settings):
    return copy.deepcopy({key: settings.get(key, {} if key == 'source_headers' else '') for key in SOURCE_FIELDS})

def public_source_url(url):
    if not url:
        return ''
    parsed = urlsplit(url)
    return url if not parsed.query and not parsed.username and not parsed.password else ''

def public_record(record):
    if record is None:
        return None
    result = {key: value for key, value in record.items() if key not in {'source_url', 'source_config'}}
    from .titles import recording_title, download_filename
    result['title'] = recording_title(record)
    result['filename'] = download_filename(record)
    url = record.get('source_config', {}).get('source_url', record.get('source_url', ''))
    safe_url = public_source_url(url)
    if safe_url:
        result['source_url'] = safe_url
    return result

def public_source(settings):
    result = dict(settings)
    for key in SECRET_FIELDS:
        result.pop(key, None)
        result[key + '_configured'] = bool(settings.get(key))
    # Signed URLs are secrets too. They are replaced explicitly, never reflected.
    result['source_configured'] = bool(settings.get('source_url'))
    url = settings.get('source_url', '')
    result['source_url'] = public_source_url(url)
    return result

def update_source(settings, body):
    result = dict(settings)
    for key in SOURCE_FIELDS:
        if key in body:
            value = body[key]
            if key in SECRET_FIELDS and value == '********':
                raise ValueError('Masked credentials cannot be saved; omit to retain')
            if value == '' and key in SECRET_FIELDS:
                continue
            result[key] = value
    clear = body.get('clear_source_secrets', [])
    if not isinstance(clear, list) or any(key not in SECRET_FIELDS for key in clear):
        raise ValueError('clear_source_secrets must name source secret fields')
    for key in clear:
        result[key] = {} if key == 'source_headers' else ''
    config = source_config(result)
    if not isinstance(config['source_url'], str) or (config['source_url'] and not valid_url(config['source_url'])):
        raise ValueError('Source must be HTTP(S), without URL credentials or fragments')
    for key in ('source_basic_username', 'source_basic_password', 'source_bearer_token'):
        if not isinstance(config[key], str) or len(config[key]) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in config[key]):
            raise ValueError('Source credentials must be text without control characters')
    if ':' in config['source_basic_username']:
        raise ValueError('Basic username cannot contain a colon')
    if any(ord(c) > 126 or c.isspace() for c in config['source_bearer_token']):
        raise ValueError('Bearer token must contain printable ASCII without whitespace')
    headers = config['source_headers']
    if not isinstance(headers, dict) or len(headers) > 32:
        raise ValueError('Source headers must be an object with at most 32 entries')
    names = set()
    for name, value in headers.items():
        if not isinstance(name, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) or name.lower() in names:
            raise ValueError('Source header names must be valid and unique')
        names.add(name.lower())
        if name.lower() in {'host', 'content-length', 'transfer-encoding', 'connection', 'range', 'proxy-authorization', 'accept-encoding'}:
            raise ValueError('Transport-controlled source header is not allowed')
        if not isinstance(value, str) or len(value) > 8192 or any(ord(c) < 32 or ord(c) > 126 for c in value):
            raise ValueError('Source header values must be printable ASCII text')
    if sum((bool(config['source_basic_username']), bool(config['source_bearer_token']), 'authorization' in names)) > 1:
        raise ValueError('Choose only one of Basic, bearer, or custom Authorization')
    return result
