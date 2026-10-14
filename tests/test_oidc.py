"""Local signed OIDC provider exercised via the recorder's public HTTP boundary."""
import time
import unittest
from urllib.parse import urlsplit, parse_qs
from aiohttp import web
from aiohttp.test_utils import TestServer
from joserfc import jwt
from joserfc.jwk import RSAKey
try:
    from . import test_auth
except ImportError:
    import test_auth


class OIDCTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await test_auth.AuthTests.asyncSetUp(self)
        self.key = RSAKey.generate_key(2048, parameters={'kid':'fixture'})
        self.groups = ['operators']
        self.bad = None
        self.nonce = None
        app = web.Application()
        async def discovery(request):
            base = self.issuer.rstrip('/')
            return web.json_response({'issuer':self.issuer, 'authorization_endpoint':base+'/authorize', 'token_endpoint':base+'/token', 'jwks_uri':base+'/keys'})
        async def keys(request):
            return web.json_response({'keys':[self.key.as_dict(private=False)]})
        async def token(request):
            form = await request.post()
            if not form.get('code_verifier'):
                raise web.HTTPBadRequest()
            claims = {'iss':self.issuer, 'aud':'recorder', 'sub':'fixture-user', 'iat':int(time.time()), 'exp':int(time.time())+300, 'nonce':self.nonce, 'groups':self.groups}
            if self.bad == 'exp':
                claims['exp'] = int(time.time())-600
            elif self.bad and self.bad != 'signature':
                claims[self.bad] = 'invalid'
            signing_key = RSAKey.generate_key(2048) if self.bad == 'signature' else self.key
            return web.json_response({'access_token':'fixture-access', 'token_type':'Bearer', 'id_token':jwt.encode({'alg':'RS256','kid':'fixture'}, claims, signing_key)})
        app.router.add_get('/.well-known/openid-configuration', discovery)
        app.router.add_get('/keys', keys)
        app.router.add_post('/token', token)
        self.provider = TestServer(app)
        await self.provider.start_server()
        self.issuer = str(self.provider.make_url('')).rstrip('/')
        await self.client.post('/api/auth/oidc/config', json={'issuer':self.issuer, 'client_id':'recorder', 'client_secret':'fixture-secret', 'public_url':str(self.client.make_url('')).rstrip('/'), 'operator_groups':['operators'], 'admin_groups':['admins'], 'enabled':True})
        await self.client.post('/api/auth/config', json={'username':'admin','password':'correct horse battery staple','enabled':True})

    async def asyncTearDown(self):
        await self.provider.close()
        await test_auth.AuthTests.asyncTearDown(self)

    async def sso(self):
        response = await self.client.get('/api/auth/oidc/login', allow_redirects=False)
        self.assertEqual(response.status, 302)
        query = parse_qs(urlsplit(response.headers['Location']).query)
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.nonce = query['nonce'][0]
        return await self.client.get('/api/auth/oidc/callback', params={'state':query['state'][0], 'code':'fixture-code'}, allow_redirects=False)

    async def test_signed_operator_and_role_overlap(self):
        self.assertEqual((await self.sso()).status, 302)
        self.assertEqual((await self.client.get('/api/auth/status')).status, 200)
        self.assertEqual((await self.client.get('/api/settings')).status, 403)
        self.groups = ['operators','admins']
        self.assertEqual((await self.sso()).status, 302)
        self.assertEqual((await self.client.get('/api/settings')).status, 200)

    async def test_trailing_slash_issuer_is_preserved(self):
        response = await self.client.post('/api/auth/login', json={'username':'admin','password':'correct horse battery staple'})
        csrf = {'X-CSRF-Token':(await response.json())['csrf_token']}
        self.issuer += '/'
        response = await self.client.post('/api/auth/oidc/config', json={'issuer':self.issuer}, headers=csrf)
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['issuer'], self.issuer)
        self.assertEqual((await self.sso()).status, 302)

    async def test_unmapped_and_invalid_claims_denied_with_local_recovery(self):
        self.groups = ['unmapped']
        self.assertEqual((await self.sso()).status, 403)
        self.groups = ['admins']
        for claim in ('nonce','iss','aud','exp','signature'):
            self.bad = claim
            self.assertEqual((await self.sso()).status, 401)
        await self.provider.close()
        self.assertEqual((await self.client.get('/api/auth/oidc/login')).status, 503)
        self.assertEqual((await self.client.post('/api/auth/login', json={'username':'admin','password':'correct horse battery staple'})).status, 200)

    async def test_state_is_browser_bound_and_single_use(self):
        response = await self.client.get('/api/auth/oidc/login', allow_redirects=False)
        query = parse_qs(urlsplit(response.headers['Location']).query)
        self.nonce = query['nonce'][0]
        self.client.session.cookie_jar.clear()
        params = {'state':query['state'][0], 'code':'fixture-code'}
        self.assertEqual((await self.client.get('/api/auth/oidc/callback', params=params)).status, 401)
        self.assertEqual((await self.client.get('/api/auth/oidc/callback', params=params)).status, 401)
