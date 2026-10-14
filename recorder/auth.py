"""Optional access control. Secrets are verified, never returned from persistence."""
import hashlib
import hmac
import json
import math
import secrets
import time
import uuid
import asyncio
from urllib.parse import urlsplit
from aiohttp import web

COOKIE = 'recorder_session'
SAFE = {'GET', 'HEAD', 'OPTIONS'}
OPERATOR_PERMISSIONS = frozenset({
    'status:read', 'preview:read', 'branding:read', 'recordings:read', 'recordings:start',
    'recordings:stop', 'recordings:download', 'recordings:pin', 'recordings:title',
    'uploads:read', 'uploads:create', 'uploads:retry', 'uploads:cancel',
})
ADMIN_PERMISSIONS = OPERATOR_PERMISSIONS | frozenset({
    'settings:read', 'settings:write', 'recordings:delete', 'auth:manage',
    'branding:write', 'youtube:manage', 'webhooks:manage',
})


def operation(request):
    """One operation identity for both route versions; additions belong here."""
    path = request.path.removeprefix('/api/v1') if request.path.startswith('/api/v1/') else request.path.removeprefix('/api')
    method = request.method
    if path in {'/health', '/status'}:
        return 'status:read'
    if path == '/preview' or path.startswith('/preview/'):
        return 'preview:read'
    if path in {'/settings', '/source', '/storage'}:
        return 'settings:read' if method == 'GET' else 'settings:write'
    if path == '/branding':
        return 'branding:read' if method == 'GET' else 'branding:write'
    if path.startswith('/branding/'):
        return 'branding:read' if method == 'GET' else 'branding:write'
    if path == '/recordings':
        return 'recordings:read' if method == 'GET' else 'recordings:start'
    if path == '/stop' or path.endswith('/stop') and path.startswith('/recordings/'):
        return 'recordings:stop'
    if path.startswith('/recordings/'):
        if path.endswith('/download'):
            return 'recordings:download'
        return 'recordings:delete' if method == 'DELETE' else 'recordings:update' if method == 'PATCH' else 'recordings:read'
    if path == '/auth/status' or path == '/auth/login' or path.startswith('/auth/oidc/login') or path.startswith('/auth/oidc/callback'):
        return 'auth:public'
    if path == '/auth/logout':
        return 'auth:session'
    if path.startswith('/auth/'):
        return 'auth:manage'
    if path.startswith('/uploads'):
        if method == 'GET':
            return 'uploads:read'
        if path.endswith('/retry') or path.endswith('/requeue'):
            return 'uploads:retry'
        if path.endswith('/cancel') or path.endswith('/release'):
            return 'uploads:cancel'
        return 'uploads:create'
    if path.startswith('/youtube'):
        return 'youtube:manage'
    if path.startswith('/webhooks'):
        return 'webhooks:manage'
    return None


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
        for key in self.state['keys']:
            # Existing role-only keys keep their exact former access.
            key.setdefault('permissions', sorted(ADMIN_PERMISSIONS if key['role'] == 'admin' else OPERATOR_PERMISSIONS))
            key.setdefault('expires_at', None)
            key.setdefault('last_used_at', None)
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
        try:
            return await self.authorize(request, handler)
        except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
            error = web.HTTPBadRequest(text='Invalid request JSON or values')
            if request.path.startswith('/api/v1/'):
                return web.json_response({'error': {'code':'invalid_request', 'message':error.text}}, status=400)
            raise error from None
        except web.HTTPException as error:
            if request.path.startswith('/api/v1/'):
                codes = {400:'invalid_request', 401:'unauthorized', 403:'forbidden', 404:'not_found', 405:'method_not_allowed', 409:'conflict', 413:'too_large', 429:'rate_limited', 507:'insufficient_storage'}
                code = error.headers.get('X-Error-Code', codes.get(error.status, 'request_failed'))
                return web.json_response({'error': {'code': code, 'message': error.text or error.reason}}, status=error.status,
                                         headers={'Cache-Control':'no-store'})
            raise

    async def authorize(self, request, handler):
        op = operation(request)
        public = request.path in {'/health', '/api/health', '/api/v1/health'} or op in {'auth:public', 'branding:read'} or request.path.startswith('/static/') or request.path in {'/', '/login'}
        role = 'admin' if not self.state['enabled'] and not self.state.get('require_api_key') else None
        session = self.session(request)
        authorization = request.headers.get('Authorization')
        if authorization:
            prefix, _, value = authorization.partition(' ')
            key = next((k for k in self.state['keys'] if hmac.compare_digest(k['hash'], digest(value))), None)
            if prefix != 'Bearer' or not key or (key.get('expires_at') is not None and key['expires_at'] <= time.time()):
                raise web.HTTPUnauthorized(text='Invalid API key')
            role = key['role']
            request['caller_id'] = 'key:' + key['id']
            needed = {op} if op not in {'auth:public', None, 'recordings:update'} else set()
            if op == 'recordings:update':
                patch = await request.json()
                if not isinstance(patch, dict):
                    raise web.HTTPBadRequest(text='JSON object required')
                request['parsed_patch'] = patch
                if 'pinned' in patch:
                    needed.add('recordings:pin')
                if 'title' in patch:
                    needed.add('recordings:title')
            if not needed.issubset(set(key['permissions'])):
                raise web.HTTPForbidden(text='API key lacks permission for this operation')
            key['last_used_at'] = time.time()
            self.save()
        elif session and session['expires'] > time.time():
            role = session['role']
            request['caller_id'] = 'session:' + digest(request.cookies.get(COOKIE, ''))
            if not public:
                self.csrf(request, session)
        else:
            request['caller_id'] = 'local'
        request['role'] = role
        if not public:
            if role is None:
                raise web.HTTPUnauthorized(text='Sign in required')
            if op in ADMIN_PERMISSIONS - OPERATOR_PERMISSIONS and role != 'admin':
                raise web.HTTPForbidden(text='Administrator permission required')
        try:
            response = await handler(request)
            if '/auth/' in request.path or '/youtube/' in request.path:
                response.headers['Cache-Control'] = 'no-store'
            return response
        except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
            raise web.HTTPBadRequest(text='Invalid request JSON or values')

    async def status(self, request):
        session = self.session(request)
        valid = session and session['expires'] > time.time()
        browser_login_required = self.state['enabled'] or self.state.get('require_api_key', False)
        return web.json_response({'enabled': browser_login_required, 'role': session['role'] if valid else ('admin' if not browser_login_required else None), 'csrf_token':session['csrf_token'] if valid else None, 'sso_enabled':bool(self.state.get('oidc', {}).get('enabled'))})

    async def permissions(self, request):
        self.require_admin(request)
        return web.json_response({'operator': sorted(OPERATOR_PERMISSIONS), 'admin': sorted(ADMIN_PERMISSIONS)})

    async def config(self, request):
        self.require_admin(request)
        if request.method == 'GET':
            return web.json_response({'enabled':self.state['enabled'], 'username':(self.state['admin'] or {}).get('username'), 'public_url':self.state.get('public_url', ''), 'require_api_key':self.state.get('require_api_key', False)})
        body = await request.json()
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text='JSON object required')
        changes = {}
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
            changes['public_url'] = value.rstrip('/')
        if 'password' in body or 'username' in body:
            username, password = body.get('username'), body.get('password')
            if not isinstance(username, str) or not username.strip() or not isinstance(password, str) or not 12 <= len(password) <= 1024:
                raise web.HTTPBadRequest(text='Administrator username and password of at least 12 characters required')
            salt = secrets.token_hex(16)
            changes['admin'] = {'username':username.strip(), 'salt':salt, 'hash':await asyncio.to_thread(password_hash, password, salt)}
        # Hashing yields to concurrent key/SSO updates. Snapshot current state only
        # after the last await, then validate and save this transition atomically.
        updated = {**self.state, **changes}
        enabled = body.get('enabled', updated['enabled'])
        if not isinstance(enabled, bool) or (enabled and not updated['admin']):
            raise web.HTTPBadRequest(text='Create a local administrator before enabling authentication')
        require_key = body.get('require_api_key', updated.get('require_api_key', False))
        if not isinstance(require_key, bool) or (require_key and not updated['admin']):
            raise web.HTTPBadRequest(text='Configure a local recovery administrator before requiring API keys')
        updated['require_api_key'] = require_key
        updated['enabled'] = enabled
        previously_enabled = self.state['enabled']
        self.state = updated
        self.save()
        if 'password' in body or enabled != previously_enabled:
            self.sessions.clear()
        return web.json_response({'enabled':enabled, 'username':(updated['admin'] or {}).get('username'), 'require_api_key':require_key})

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
        available = ADMIN_PERMISSIONS if role == 'admin' else OPERATOR_PERMISSIONS
        permissions = body.get('permissions', sorted(available))
        if not isinstance(permissions, list) or not permissions or any(not isinstance(p, str) or p not in available for p in permissions) or len(set(permissions)) != len(permissions):
            raise web.HTTPBadRequest(text='Permissions must be a nonempty, unique list allowed for the role')
        expires_at = body.get('expires_at')
        if expires_at is not None and (isinstance(expires_at, bool) or not isinstance(expires_at, (float, int)) or not math.isfinite(expires_at) or expires_at <= time.time()):
            raise web.HTTPBadRequest(text='expires_at must be a future Unix timestamp')
        raw = secrets.token_urlsafe(32)
        key = {'id':uuid.uuid4().hex, 'label':label.strip(), 'role':role, 'permissions':permissions,
               'expires_at':expires_at, 'last_used_at':None, 'created_at':time.time(), 'hash':digest(raw)}
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
    app.router.add_get('/api/auth/permissions', access.permissions)
    app.router.add_post('/api/auth/keys', access.keys)
    app.router.add_delete('/api/auth/keys/{ident}', access.keys)
    app.router.add_get('/api/auth/oidc/config', oidc.config)
    app.router.add_post('/api/auth/oidc/config', oidc.config)
    app.router.add_get('/api/auth/oidc/login', oidc.login)
    app.router.add_get('/api/auth/oidc/callback', oidc.callback)
    app.router.add_get('/api/v1/auth/status', access.status)
    app.router.add_get('/api/v1/auth/config', access.config)
    app.router.add_post('/api/v1/auth/config', access.config)
    app.router.add_post('/api/v1/auth/login', access.login)
    app.router.add_post('/api/v1/auth/logout', access.logout)
    app.router.add_get('/api/v1/auth/keys', access.keys)
    app.router.add_get('/api/v1/auth/permissions', access.permissions)
    app.router.add_post('/api/v1/auth/keys', access.keys)
    app.router.add_delete('/api/v1/auth/keys/{ident}', access.keys)
    app.router.add_get('/api/v1/auth/oidc/config', oidc.config)
    app.router.add_post('/api/v1/auth/oidc/config', oidc.config)
    app.router.add_get('/api/v1/auth/oidc/login', oidc.login)
    app.router.add_get('/api/v1/auth/oidc/callback', oidc.callback)
    return access
