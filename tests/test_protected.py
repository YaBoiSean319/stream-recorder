import tempfile
import unittest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from recorder.server import create_app

class ProtectedSourceHTTP(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_source_configuration_returns_actionable_bad_request(self):
        with tempfile.TemporaryDirectory() as data:
            async with TestClient(TestServer(create_app(data))) as client:
                for body in ({'source_url': []}, {'source_url': {'url': 'https://example.org'}}, {'source_url': None}, {'source_url': 'https://@example.org/stream'}, {'source_url': 'https://example.org:invalid/stream'}, {'source_url': 'https://example.org:99999/stream'}, {'source_bearer_token': 'invalid token'}, {'source_headers': {'X-Test': 'evil\r\nInjected: yes'}}, {'source_headers': []}, {'source_headers': {'Host': 'other.example'}}):
                    response = await client.put('/api/settings', json=body)
                    self.assertEqual(response.status, 400, body)
    async def test_masked_credentials_retained_and_used_by_preview(self):
        async def source(request):
            if request.headers.get('Authorization') != 'Bearer top-secret':
                raise web.HTTPUnauthorized()
            return web.Response(text='#EXTM3U\n#EXTINF:1,\nsegment.ts\n')
        upstream = web.Application(); upstream.router.add_get('/{tail:.*}', source)
        async with TestServer(upstream) as server:
            with tempfile.TemporaryDirectory() as data:
                async with TestClient(TestServer(create_app(data))) as client:
                    response = await client.put('/api/settings', json={'source_url': str(server.make_url('/signed?token=private')), 'source_bearer_token': 'top-secret'})
                    self.assertEqual(response.status, 200)
                    result = await response.json()
                    self.assertNotIn('top-secret', str(result))
                    self.assertNotIn('private', str(result))
                    await client.put('/api/settings', json={'storage_cap_bytes': 200000000})
                    result = await (await client.get('/api/settings')).json()
                    self.assertTrue(result['source_bearer_token_configured'])
                    self.assertEqual((await client.get(result['preview_url'])).status, 200)
                    response = await client.put('/api/settings', json={'source_basic_username': 'user'})
                    self.assertEqual(response.status, 400)
                    response = await client.put('/api/settings', json={'clear_source_secrets': ['source_bearer_token']})
                    result = await response.json()
                    self.assertFalse(result['source_bearer_token_configured'])
                    self.assertEqual((await client.get(result['preview_url'])).status, 502)
