"""Configured-source HTTP boundary. Credentials never leave their configured origin."""
import asyncio
import base64
import copy
import re
import secrets
import time
from urllib.parse import urljoin, urlsplit

import aiohttp
from aiohttp import web

MAX_RESOURCE = 64 * 1024 * 1024
MAX_PLAYLIST = 2 * 1024 * 1024

def valid_url(value):
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {'http', 'https'} and bool(parsed.hostname) and not parsed.username and not parsed.password and not parsed.fragment and not any(ord(c) < 33 for c in value)
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
            if ref == (url, config, capture, base):
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
        self.refs[token] = (url, config, capture, base)
        self.touched[token] = now
        if not capture:
            self.preview_tokens.add(token)
        return token

    def invalidate_preview(self):
        for token in self.preview_tokens:
            self.refs.pop(token, None)
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
        if ref is None or ref[2] != internal:
            raise web.HTTPNotFound(text='Unknown or expired source reference')
        url, config, capture, base = ref
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
