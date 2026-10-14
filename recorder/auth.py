"""Optional access control. Secrets are verified, never returned from persistence."""
import hashlib
import hmac
import json
import secrets
import time
import uuid
import asyncio
from urllib.parse import urlsplit
from aiohttp import web

COOKIE = 'recorder_session'
SAFE = {'GET', 'HEAD', 'OPTIONS'}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


class Access:
    def __init__(self, service):
        self.service = service
        self.db = service.db
        self.db.execute('CREATE TABLE IF NOT EXISTS auth_state(id INTEGER PRIMARY KEY,payload TEXT)')
        row = self.db.execute('SELECT payload FROM auth_state WHERE id=1').fetchone()
        self.state = json.loads(row[0]) if row else {'enabled': False, 'admin': None, 'keys': []}
        self.sessions = {}
        self.attempts = {}

    def save(self):
        self.db.execute('INSERT OR REPLACE INTO auth_state VALUES(1,?)', (json.dumps(self.state),))
        self.db.commit()

    def session(self, request):
        return self.sessions.get(digest(request.cookies.get(COOKIE, '')))

    def csrf(self, request, session):
        if request.method not in SAFE and not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), session['csrf_token']):
            raise web.HTTPForbidden(text='CSRF token required')
        self.origin(request)

    def origin(self, request):
        origin = request.headers.get('Origin')
        expected = self.state.get('public_url') or self.state.get('oidc', {}).get('public_url') or f'{request.scheme}://{request.host}'
        if origin and origin != expected:
            raise web.HTTPForbidden(text='Origin does not match this application')

    def require_admin(self, request):
        if request.get('role') != 'admin':
            raise web.HTTPForbidden(text='Administrator permission required')

    @web.middleware
    async def middleware(self, request, handler):
        public = request.path in {'/health', '/api/health', '/api/auth/status', '/api/auth/login', '/api/auth/oidc/login', '/api/auth/oidc/callback'} or request.path.startswith('/static/') or request.path in {'/', '/login'}
        role = 'admin' if not self.state['enabled'] else None
        session = self.session(request)
        authorization = request.headers.get('Authorization')
        if authorization:
            prefix, _, value = authorization.partition(' ')
            key = next((k for k in self.state['keys'] if hmac.compare_digest(k['hash'], digest(value))), None)
            if prefix != 'Bearer' or not key:
                raise web.HTTPUnauthorized(text='Invalid API key')
            role = key['role']
        elif session and session['expires'] > time.time():
            role = session['role']
            if not public:
                self.csrf(request, session)
        request['role'] = role
        if not public:
            if role is None:
                raise web.HTTPUnauthorized(text='Sign in required')
            admin_operation = request.path in {'/api/settings', '/api/auth/config', '/api/auth/keys', '/api/auth/oidc/config'}
            admin_operation |= request.method == 'DELETE' or request.path.startswith('/api/auth/keys/')
            if admin_operation and role != 'admin':
                raise web.HTTPForbidden(text='Administrator permission required')
        try:
            response = await handler(request)
            if request.path.startswith('/api/auth/'):
                response.headers['Cache-Control'] = 'no-store'
            return response
        except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
            raise web.HTTPBadRequest(text='Invalid request JSON or values')

    async def status(self, request):
        session = self.session(request)
        valid = session and session['expires'] > time.time()
        return web.json_response({'enabled': self.state['enabled'], 'role': session['role'] if valid else ('admin' if not self.state['enabled'] else None), 'csrf_token':session['csrf_token'] if valid else None, 'sso_enabled':bool(self.state.get('oidc', {}).get('enabled'))})

    async def config(self, request):
        self.require_admin(request)
        if request.method == 'GET':
            return web.json_response({'enabled':self.state['enabled'], 'username':(self.state['admin'] or {}).get('username'), 'public_url':self.state.get('public_url', '')})
        body = await request.json()
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text='JSON object required')
        updated = dict(self.state)
        if 'public_url' in body:
            value = body['public_url']
            parsed = urlsplit(value) if isinstance(value, str) else None
            try:
                if parsed:
                    parsed.port  # Reject invalid ports before persisting cookie/origin policy.
            except ValueError:
                raise web.HTTPBadRequest(text='Public URL must be an HTTPS origin') from None
            if not parsed or parsed.scheme != 'https' or not parsed.hostname or parsed.path not in {'', '/'} or parsed.query or parsed.fragment or parsed.username:
                raise web.HTTPBadRequest(text='Public URL must be an HTTPS origin')
            updated['public_url'] = value.rstrip('/')
        if 'password' in body or 'username' in body:
            username, password = body.get('username'), body.get('password')
            if not isinstance(username, str) or not username.strip() or not isinstance(password, str) or not 12 <= len(password) <= 1024:
                raise web.HTTPBadRequest(text='Administrator username and password of at least 12 characters required')
            salt = secrets.token_hex(16)
            updated['admin'] = {'username':username.strip(), 'salt':salt, 'hash':await asyncio.to_thread(password_hash, password, salt)}
        enabled = body.get('enabled', updated['enabled'])
        if not isinstance(enabled, bool) or (enabled and not updated['admin']):
            raise web.HTTPBadRequest(text='Create a local administrator before enabling authentication')
        updated['enabled'] = enabled
        previously_enabled = self.state['enabled']
        self.state = updated
        self.save()
        if 'password' in body or enabled != previously_enabled:
            self.sessions.clear()
        return web.json_response({'enabled':enabled, 'username':(updated['admin'] or {}).get('username')})

    def sign_in(self, request, role):
        self.sessions = {k:v for k,v in self.sessions.items() if v['expires'] > time.time()}
        raw = secrets.token_urlsafe(32)
        session = {'role':role, 'csrf_token':secrets.token_urlsafe(32), 'expires':time.time() + 8 * 3600}
        self.sessions[digest(raw)] = session
        response = web.json_response({'role':role, 'csrf_token':session['csrf_token']})
        public_url = self.state.get('public_url') or self.state.get('oidc', {}).get('public_url', '')
        response.set_cookie(COOKIE, raw, httponly=True, secure=request.secure or public_url.startswith('https://'), samesite='Lax', max_age=8 * 3600, path='/')
        response.headers['Cache-Control'] = 'no-store'
        return response

    async def login(self, request):
        self.origin(request)
        body = await request.json()
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text='JSON object required')
        slot = self.attempts.setdefault(request.remote, [])
        slot[:] = [t for t in slot if t > time.time() - 60]
        if len(slot) >= 10:
            raise web.HTTPTooManyRequests(text='Too many sign-in attempts; try again in one minute')
        slot.append(time.time())
        admin = self.state['admin']
        password = body.get('password')
        if not admin or not isinstance(password, str) or len(password) > 1024 or body.get('username') != admin['username'] or not hmac.compare_digest(await asyncio.to_thread(password_hash, password, admin['salt']), admin['hash']):
            raise web.HTTPUnauthorized(text='Invalid username or password')
        return self.sign_in(request, 'admin')

    async def logout(self, request):
        self.sessions.pop(digest(request.cookies.get(COOKIE, '')), None)
        response = web.json_response({'signed_out':True})
        response.del_cookie(COOKIE, path='/')
        return response

    async def keys(self, request):
        self.require_admin(request)
        if request.method == 'GET':
            return web.json_response([{k:v for k,v in key.items() if k != 'hash'} for key in self.state['keys']])
        if request.method == 'DELETE':
            ident = request.match_info['ident']
            if not any(k['id'] == ident for k in self.state['keys']):
                raise web.HTTPNotFound(text='Unknown API key')
            self.state['keys'] = [k for k in self.state['keys'] if k['id'] != ident]
            self.save()
            return web.json_response({'revoked':ident})
        body = await request.json()
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text='JSON object required')
        role, label = body.get('role', 'operator'), body.get('label')
        if role not in {'operator', 'admin'} or not isinstance(label, str) or not label.strip() or len(label) > 200:
            raise web.HTTPBadRequest(text='A label and operator/admin role are required')
        raw = secrets.token_urlsafe(32)
        key = {'id':uuid.uuid4().hex, 'label':label.strip(), 'role':role, 'created_at':time.time(), 'hash':digest(raw)}
        self.state['keys'].append(key)
        self.save()
        response = web.json_response({k:v for k,v in key.items() if k != 'hash'} | {'key':raw}, status=201)
        response.headers['Cache-Control'] = 'no-store'
        return response


def install(app, service):
    access = Access(service)
    from recorder.oidc import OIDC
    oidc = OIDC(access)
    app.middlewares.append(access.middleware)
    app.router.add_get('/api/auth/status', access.status)
    app.router.add_get('/api/auth/config', access.config)
    app.router.add_post('/api/auth/config', access.config)
    app.router.add_post('/api/auth/login', access.login)
    app.router.add_post('/api/auth/logout', access.logout)
    app.router.add_get('/api/auth/keys', access.keys)
    app.router.add_post('/api/auth/keys', access.keys)
    app.router.add_delete('/api/auth/keys/{ident}', access.keys)
    app.router.add_get('/api/auth/oidc/config', oidc.config)
    app.router.add_post('/api/auth/oidc/config', oidc.config)
    app.router.add_get('/api/auth/oidc/login', oidc.login)
    app.router.add_get('/api/auth/oidc/callback', oidc.callback)
    return access
