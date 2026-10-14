"""OIDC authorization-code flow: server-held state, browser binding and PKCE."""
import secrets
import time
from urllib.parse import urlsplit
import httpx
from aiohttp import web
from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.oidc.core import CodeIDToken
from joserfc import jwt
from joserfc.jwk import KeySet

BINDING = 'recorder_oidc'


def secure_or_loopback_url(parsed, *, allow_loopback_http=True):
    return bool(parsed and parsed.hostname and not parsed.username and not parsed.fragment and (
        parsed.scheme == 'https' or allow_loopback_http and parsed.scheme == 'http'
        and parsed.hostname in {'localhost', '127.0.0.1', '::1'}
    ))


class OIDC:
    def __init__(self, access):
        self.access = access
        self.pending = {}

    async def config(self, request):
        self.access.require_admin(request)
        config = dict(self.access.state.get('oidc', {'enabled':False, 'group_claim':'groups', 'admin_groups':[], 'operator_groups':[]}))
        if request.method == 'POST':
            body = await request.json()
            if not isinstance(body, dict):
                raise web.HTTPBadRequest(text='JSON object required')
            for name in {'issuer', 'client_id', 'public_url', 'group_claim', 'operator_groups', 'admin_groups', 'enabled'}:
                if name in body:
                    config[name] = body[name]
            if body.get('remove_client_secret'):
                config.pop('client_secret', None)
            if body.get('client_secret'):
                if not isinstance(body['client_secret'], str):
                    raise web.HTTPBadRequest(text='Client secret must be text')
                config['client_secret'] = body['client_secret']
            if not isinstance(config['enabled'], bool):
                raise web.HTTPBadRequest(text='Enabled must be boolean')
            for name in ('admin_groups', 'operator_groups'):
                if not isinstance(config[name], list) or not all(isinstance(g, str) and g for g in config[name]):
                    raise web.HTTPBadRequest(text='Group mappings must be lists of nonempty group names')
            if config['enabled']:
                for name in ('issuer', 'public_url'):
                    value = config.get(name)
                    parsed = urlsplit(value) if isinstance(value, str) else None
                    # Loopback HTTP is allowed only for isolated local development/fixtures.
                    if not secure_or_loopback_url(parsed) or parsed.query:
                        raise web.HTTPBadRequest(text=f'{name} must be HTTPS (HTTP only on loopback)')
                if not config.get('client_id') or not isinstance(config['client_id'], str) or not isinstance(config.get('group_claim'), str) or not config['group_claim']:
                    raise web.HTTPBadRequest(text='Client ID and group claim are required')
                if not config['admin_groups'] and not config['operator_groups']:
                    raise web.HTTPBadRequest(text='At least one explicit group mapping is required')
                config['public_url'] = config['public_url'].rstrip('/')
            self.access.state['oidc'] = config
            self.access.save()
            self.pending.clear()
        return web.json_response({k:v for k,v in config.items() if k != 'client_secret'} | {'client_secret_saved':bool(config.get('client_secret'))})

    async def metadata(self, config):
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
            response = await client.get(config['issuer'].rstrip('/')+'/.well-known/openid-configuration')
            response.raise_for_status()
            metadata = response.json()
        if metadata.get('issuer') != config['issuer']:
            raise ValueError('Issuer mismatch')
        for name in ('authorization_endpoint', 'token_endpoint', 'jwks_uri'):
            parsed = urlsplit(metadata[name])
            if not secure_or_loopback_url(parsed, allow_loopback_http=not config['issuer'].startswith('https:')):
                raise ValueError('Unsafe provider endpoint')
        return metadata

    def client(self, config, **kwargs):
        return AsyncOAuth2Client(config['client_id'], config.get('client_secret'), scope='openid profile email', redirect_uri=config['public_url']+'/api/auth/oidc/callback', code_challenge_method='S256', timeout=10, trust_env=False, **kwargs)

    async def login(self, request):
        config = self.access.state.get('oidc', {})
        if not config.get('enabled') or not self.access.state['enabled']:
            raise web.HTTPBadRequest(text='SSO is not enabled')
        try:
            metadata = await self.metadata(config)
            nonce, verifier, binding = (secrets.token_urlsafe(32) for _ in range(3))
            async with self.client(config) as client:
                uri, state = client.create_authorization_url(metadata['authorization_endpoint'], nonce=nonce, code_verifier=verifier)
            self.pending = {k:v for k,v in self.pending.items() if v['expires'] > time.time()}
            self.pending[state] = {'nonce':nonce, 'verifier':verifier, 'binding':binding, 'expires':time.time()+300, 'config':dict(config), 'metadata':metadata}
            response = web.HTTPFound(uri)
            response.set_cookie(BINDING, binding, httponly=True, secure=config['public_url'].startswith('https://'), samesite='Lax', max_age=300, path='/api/auth/oidc')
            return response
        except Exception:
            raise web.HTTPServiceUnavailable(text='Identity provider unavailable; use local administrator sign-in')

    async def callback(self, request):
        state = request.query.get('state', '')
        flow = self.pending.pop(state, None)
        if not flow or flow['expires'] <= time.time() or not secrets.compare_digest(flow['binding'], request.cookies.get(BINDING, '')):
            raise web.HTTPUnauthorized(text='Invalid or expired SSO request')
        try:
            config, metadata = flow['config'], flow['metadata']
            async with self.client(config, state=state) as client:
                token = await client.fetch_token(metadata['token_endpoint'], authorization_response=config['public_url']+str(request.rel_url), code_verifier=flow['verifier'])
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                response = await client.get(metadata['jwks_uri'])
                response.raise_for_status()
                keys = KeySet.import_key_set(response.json())
            decoded = jwt.decode(token['id_token'], keys, algorithms=['RS256', 'ES256'])
            claims = CodeIDToken(decoded.claims, decoded.header, options={'iss':{'essential':True,'value':config['issuer']}, 'aud':{'essential':True,'value':config['client_id']}}, params={'nonce':flow['nonce'], 'client_id':config['client_id'], 'access_token':token.get('access_token')})
            claims.validate(leeway=30)
            groups = claims.get(config['group_claim'], [])
            if not isinstance(groups, list) or not all(isinstance(g,str) for g in groups):
                raise ValueError('Groups missing')
            role = 'admin' if set(groups) & set(config['admin_groups']) else 'operator' if set(groups) & set(config['operator_groups']) else None
            if not role:
                raise web.HTTPForbidden(text='No mapped recorder role')
            response = self.access.sign_in(request, role)
            response.set_status(302)
            response.headers['Location'] = '/'
            response.del_cookie(BINDING, path='/api/auth/oidc')
            return response
        except web.HTTPForbidden:
            raise
        except Exception:
            raise web.HTTPUnauthorized(text='SSO validation failed; use local administrator sign-in')
