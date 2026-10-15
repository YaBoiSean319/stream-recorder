"""One shared YouTube authorization, independent of application sign-in."""
import json
import os
import secrets
import time
from urllib.parse import urlsplit

import httpx
from aiohttp import web
from authlib.integrations.httpx_client import AsyncOAuth2Client

from .oidc import secure_or_loopback_url

SCOPES = 'https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly'
COOKIE = 'recorder_youtube_flow'
DEFAULT_ENDPOINTS = {
    'authorization': 'https://accounts.google.com/o/oauth2/v2/auth',
    'token': 'https://oauth2.googleapis.com/token',
    'channels': 'https://www.googleapis.com/youtube/v3/channels',
}


class YouTube:
    def __init__(self, service):
        self.service = service
        self.db = service.db
        self.db.execute('CREATE TABLE IF NOT EXISTS youtube_connection(id INTEGER PRIMARY KEY,payload TEXT NOT NULL)')
        row = self.db.execute('SELECT payload FROM youtube_connection WHERE id=1').fetchone()
        self.data = json.loads(row[0]) if row else {'client_id': '', 'public_url': '', 'channel': None, 'reconnect_required': False}
        self.pending = {}
        self.db.commit()
        os.chmod(service.root, 0o700)
        os.chmod(service.root / 'state.sqlite', 0o600)

    def save(self):
        self.db.execute('INSERT OR REPLACE INTO youtube_connection VALUES(1,?)', (json.dumps(self.data),))
        self.db.commit()

    def endpoints(self, request):
        return request.app.get('youtube_provider', DEFAULT_ENDPOINTS)

    def client(self, *, state=None):
        return AsyncOAuth2Client(self.data['client_id'], self.data.get('client_secret'),
                                 scope=SCOPES, redirect_uri=self.callback_url(),
                                 code_challenge_method='S256', state=state,
                                 timeout=15, trust_env=False)

    def callback_url(self):
        return self.data.get('public_url', '').rstrip('/') + '/api/v1/youtube/callback'

    def public(self):
        return {'connected': bool(self.data.get('channel')), 'channel': self.data.get('channel'),
                'reconnect_required': self.data.get('reconnect_required', False),
                'error': self.data.get('error'), 'client_id': self.data.get('client_id', ''),
                'client_secret_saved': bool(self.data.get('client_secret')),
                'public_url': self.data.get('public_url', ''),
                'callback_url': self.callback_url() if self.data.get('public_url') else ''}

    async def config(self, request):
        if request.method == 'GET':
            return web.json_response(self.public())
        body = await request.json()
        if not isinstance(body, dict) or set(body) - {'client_id', 'client_secret', 'public_url', 'remove_client_secret'}:
            raise web.HTTPBadRequest(text='Supply a JSON object with client_id, client_secret, or public_url')
        changes = {}
        if 'client_id' in body:
            value = body['client_id']
            if not isinstance(value, str) or len(value) > 512:
                raise web.HTTPBadRequest(text='Client ID must be text up to 512 characters')
            changes['client_id'] = value.strip()
        if 'public_url' in body:
            value = body['public_url']
            parsed = urlsplit(value) if isinstance(value, str) else None
            try:
                if parsed:
                    parsed.port
            except ValueError:
                raise web.HTTPBadRequest(text='Public URL must be an HTTPS origin') from None
            if not secure_or_loopback_url(parsed) or parsed.path not in {'', '/'} or parsed.query or parsed.fragment:
                raise web.HTTPBadRequest(text='Public URL must be an HTTPS origin (HTTP only for local loopback)')
            changes['public_url'] = value.rstrip('/')
        if body.get('remove_client_secret'):
            changes['client_secret'] = None
        if 'client_secret' in body and body['client_secret']:
            if not isinstance(body['client_secret'], str) or len(body['client_secret']) > 2048:
                raise web.HTTPBadRequest(text='Client secret must be text up to 2048 characters')
            changes['client_secret'] = body['client_secret']
        if any(self.data.get(k) != v for k, v in changes.items() if k in {'client_id', 'client_secret', 'public_url'}):
            self.data.update({'channel': None, 'token': None, 'reconnect_required': False, 'error': None})
        self.data.update(changes)
        self.pending.clear()
        self.save()
        return web.json_response(self.public())

    async def status(self, request):
        return web.json_response(self.public())

    async def connect(self, request):
        # Interactive Google consent must be started in an administrator browser.
        if request.headers.get('Authorization') or request.app['recorder_access'].state['enabled'] and not request.app['recorder_access'].session(request):
            raise web.HTTPForbidden(text='Connect from a signed-in Administrator browser')
        if not self.data.get('client_id') or not self.data.get('client_secret') or not self.data.get('public_url'):
            raise web.HTTPBadRequest(text='Save Google client ID, client secret, and public URL first')
        binding, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        async with self.client() as client:
            url, state = client.create_authorization_url(self.endpoints(request)['authorization'],
                        code_verifier=verifier, access_type='offline', prompt='consent')
        self.pending = {k: v for k, v in self.pending.items() if v['expires'] > time.time()}
        self.pending[state] = {'binding': binding, 'verifier': verifier, 'expires': time.time()+300}
        response = web.json_response({'authorization_url': url})
        response.set_cookie(COOKIE, binding, httponly=True, samesite='Lax',
                            secure=self.data['public_url'].startswith('https://'),
                            path='/api/v1/youtube', max_age=300)
        return response

    async def callback(self, request):
        flow = self.pending.pop(request.query.get('state', ''), None)
        if not flow or flow['expires'] < time.time() or not secrets.compare_digest(flow['binding'], request.cookies.get(COOKIE, '')):
            raise web.HTTPUnauthorized(text='Invalid or expired YouTube connection; return to Settings and connect again')
        if 'error' in request.query:
            raise web.HTTPBadRequest(text='Google declined authorization; return to Settings and connect again')
        try:
            async with self.client(state=request.query['state']) as client:
                token = await client.fetch_token(self.endpoints(request)['token'],
                    authorization_response=self.callback_url() + str(request.rel_url)[len('/api/v1/youtube/callback'):],
                    code_verifier=flow['verifier'])
            if not token.get('refresh_token'):
                raise ValueError('No offline authorization')
            async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False) as http:
                response = await http.get(self.endpoints(request)['channels'], params={'part': 'snippet', 'mine': 'true'},
                                          headers={'Authorization': 'Bearer ' + token['access_token']})
                response.raise_for_status()
                item = response.json()['items'][0]
            channel = {'id': item['id'], 'name': item['snippet']['title']}
        except Exception:
            raise web.HTTPBadGateway(text='Google connection failed or no YouTube channel was selected; reconnect in Settings') from None
        self.data.update({'channel': channel, 'token': {'refresh_token': token['refresh_token'],
                            'access_token': token['access_token'], 'expires_at': time.time()+int(token.get('expires_in', 3600))},
                          'reconnect_required': False, 'error': None})
        self.save()
        response = web.HTTPFound('/#settings')
        response.del_cookie(COOKIE, path='/api/v1/youtube')
        return response

    async def refresh(self, request):
        token = self.data.get('token') or {}
        if not token.get('refresh_token'):
            raise web.HTTPConflict(text='Connect a YouTube channel in Settings first')
        try:
            async with self.client() as client:
                updated = await client.refresh_token(self.endpoints(request)['token'], refresh_token=token['refresh_token'])
            self.data['token'] = {'access_token': updated['access_token'],
                                  'refresh_token': updated.get('refresh_token') or token['refresh_token'],
                                  'expires_at': time.time()+int(updated.get('expires_in', 3600))}
            self.data.update({'reconnect_required': False, 'error': None})
            self.save()
            return web.json_response(self.public())
        except Exception:
            self.data.update({'reconnect_required': True, 'error': 'Authorization expired or was revoked. Reconnect the channel in Settings.'})
            self.save()
            raise web.HTTPConflict(text=self.data['error']) from None

    async def disconnect(self, request):
        self.data.update({'channel': None, 'token': None, 'reconnect_required': False, 'error': None})
        self.pending.clear()
        self.save()
        return web.json_response(self.public())


def install(app, service, access):
    youtube = YouTube(service)
    app['recorder_access'] = access
    app['youtube_connection'] = youtube
    app.router.add_get('/api/v1/youtube/config', youtube.config)
    app.router.add_put('/api/v1/youtube/config', youtube.config)
    app.router.add_get('/api/v1/youtube/status', youtube.status)
    app.router.add_post('/api/v1/youtube/connect', youtube.connect)
    app.router.add_get('/api/v1/youtube/callback', youtube.callback)
    app.router.add_post('/api/v1/youtube/refresh', youtube.refresh)
    app.router.add_delete('/api/v1/youtube/connection', youtube.disconnect)
