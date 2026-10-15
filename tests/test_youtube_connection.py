"""YouTube connection behavior through the public API with a local OAuth provider."""
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from recorder.server import create_app


class YouTubeConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tokens = 0
        self.invalid_refresh = False
        provider = web.Application()

        async def token(request):
            form = await request.post()
            self.tokens += 1
            if form.get('grant_type') == 'refresh_token' and self.invalid_refresh:
                return web.json_response({'error': 'invalid_grant'}, status=400)
            if form.get('grant_type') == 'authorization_code':
                self.assertTrue(form.get('code_verifier'))
                return web.json_response({'access_token': 'fixture-access', 'refresh_token': 'fixture-refresh', 'token_type': 'Bearer', 'expires_in': 1})
            return web.json_response({'access_token': 'fixture-new-access', 'token_type': 'Bearer', 'expires_in': 3600})

        async def channels(request):
            self.assertEqual(request.query['mine'], 'true')
            self.assertEqual(request.headers['Authorization'], 'Bearer fixture-access')
            return web.json_response({'items': [{'id': 'UCfixture', 'snippet': {'title': 'Fixture channel'}}]})

        provider.router.add_post('/token', token)
        provider.router.add_get('/channels', channels)
        self.provider = TestServer(provider)
        await self.provider.start_server()
        self.app = create_app(self.tmp.name)
        self.app['youtube_provider'] = {'authorization': str(self.provider.make_url('/authorize')), 'token': str(self.provider.make_url('/token')), 'channels': str(self.provider.make_url('/channels'))}
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.origin = str(self.client.make_url('')).rstrip('/')

    async def asyncTearDown(self):
        await self.client.close()
        await self.provider.close()
        self.tmp.cleanup()

    async def connect(self):
        await self.client.put('/api/v1/youtube/config', json={'client_id': 'fixture-id', 'client_secret': 'fixture-secret', 'public_url': self.origin})
        start = await self.client.post('/api/v1/youtube/connect', json={})
        self.assertEqual(start.status, 200)
        data = await start.json()
        query = parse_qs(urlsplit(data['authorization_url']).query)
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertIn('https://www.googleapis.com/auth/youtube.upload', query['scope'][0])
        return query['state'][0]

    async def test_connect_status_secret_mask_and_restart_refresh(self):
        state = await self.connect()
        bad = await self.client.get('/api/v1/youtube/callback', params={'code': 'ok', 'state': 'wrong'}, allow_redirects=False)
        self.assertEqual(bad.status, 401)
        callback = await self.client.get('/api/v1/youtube/callback', params={'code': 'ok', 'state': state}, allow_redirects=False)
        self.assertEqual(callback.status, 302)
        status = await (await self.client.get('/api/v1/youtube/status')).json()
        self.assertEqual(status['channel'], {'id': 'UCfixture', 'name': 'Fixture channel'})
        self.assertNotIn('fixture-refresh', str(status))
        self.assertEqual((await self.client.get('/api/v1/youtube/callback', params={'code': 'ok', 'state': state})).status, 401)
        await self.client.put('/api/v1/youtube/config', json={'public_url': self.origin})
        config = await (await self.client.get('/api/v1/youtube/config')).json()
        self.assertTrue(config['client_secret_saved'])
        self.assertNotIn('fixture-secret', str(config))
        await self.client.close()
        self.app = create_app(self.tmp.name)
        self.app['youtube_provider'] = {'authorization': str(self.provider.make_url('/authorize')), 'token': str(self.provider.make_url('/token')), 'channels': str(self.provider.make_url('/channels'))}
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        status = await (await self.client.get('/api/v1/youtube/status')).json()
        self.assertEqual(status['channel']['id'], 'UCfixture')
        self.assertNotIn('fixture-refresh', str(status))
        refreshed = await self.client.post('/api/v1/youtube/refresh', json={})
        self.assertEqual(refreshed.status, 200)
        self.assertFalse((await refreshed.json())['reconnect_required'])
        self.assertNotIn('fixture-new-access', await refreshed.text())
        self.invalid_refresh = True
        response = await self.client.post('/api/v1/youtube/refresh', json={})
        self.assertEqual(response.status, 409)
        self.assertTrue((await (await self.client.get('/api/v1/youtube/status')).json())['reconnect_required'])
        self.assertEqual((await self.client.delete('/api/v1/youtube/connection')).status, 200)
        self.assertIsNone((await (await self.client.get('/api/v1/youtube/status')).json())['channel'])

    async def test_browser_binding_and_admin_permission(self):
        state = await self.connect()
        self.client.session.cookie_jar.clear()
        self.assertEqual((await self.client.get('/api/v1/youtube/callback', params={'code': 'ok', 'state': state})).status, 401)
        key = await (await self.client.post('/api/v1/auth/keys', json={'label': 'operator'})).json()
        self.assertEqual((await self.client.get('/api/v1/youtube/status', headers={'Authorization': 'Bearer '+key['key']})).status, 403)
        await self.client.post('/api/auth/config', json={'username': 'admin', 'password': 'correct horse battery staple', 'enabled': True})
        self.assertEqual((await self.client.get('/api/v1/youtube/config')).status, 401)
        self.assertEqual((await self.client.post('/api/v1/youtube/connect', json={})).status, 401)
