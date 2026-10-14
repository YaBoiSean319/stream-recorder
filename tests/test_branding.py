"""Shared branding is observable through public/versioned HTTP, across restarts."""
import tempfile
import unittest
import io
from PIL import Image
from aiohttp.test_utils import TestClient, TestServer
from recorder.server import create_app


class BrandingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        await self.open_app()

    async def open_app(self):
        self.client = TestClient(TestServer(create_app(self.tmp.name)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def test_shared_branding_is_public_but_only_admin_can_change_it(self):
        fresh = await (await self.client.get('/api/v1/branding')).json()
        self.assertEqual(fresh['name'], 'Stream Recorder')
        self.assertEqual(fresh['dark']['background'], '#141414')
        self.assertEqual(fresh['light']['accent'], '#444444')
        await self.client.put('/api/settings', json={'source_url':'https://example.org/stream.m3u8','storage_cap_bytes':90000000})
        operator = await (await self.client.post('/api/auth/keys', json={'label':'operator'})).json()
        await self.client.post('/api/auth/config', json={'username':'admin', 'password':'correct horse battery staple', 'enabled':True})
        self.assertEqual((await self.client.get('/api/v1/branding')).status, 200)
        self.assertEqual((await self.client.put('/api/v1/branding', json={'name':'Newsroom'})).status, 401)
        self.assertEqual((await self.client.put('/api/v1/branding', json={'name':'Operator edit'}, headers={'Authorization':'Bearer '+operator['key']})).status, 403)
        signed = await (await self.client.post('/api/auth/login', json={'username':'admin','password':'correct horse battery staple'})).json()
        headers = {'X-CSRF-Token':signed['csrf_token']}
        changed = await self.client.put('/api/v1/branding', json={
            'name':'Newsroom', 'dark':{'accent':'rgb(160, 170, 180)', 'surface':'#252525'}}, headers=headers)
        self.assertEqual(changed.status, 200)
        self.assertEqual((await changed.json())['dark']['accent'], '#a0aab4')
        self.assertEqual((await (await self.client.get('/api/branding')).json())['name'], 'Newsroom')
        await self.client.close()
        await self.open_app()
        self.assertEqual((await (await self.client.get('/api/v1/branding')).json())['name'], 'Newsroom')
        self.assertEqual((await self.client.put('/api/v1/branding', json={'name':'Anon'})).status, 401)
        signed = await (await self.client.post('/api/auth/login', json={'username':'admin','password':'correct horse battery staple'})).json()
        headers = {'X-CSRF-Token':signed['csrf_token']}
        self.assertEqual((await self.client.post('/api/v1/branding/reset', json={}, headers=headers)).status, 200)
        self.assertEqual((await (await self.client.get('/api/v1/branding')).json())['name'], 'Stream Recorder')
        self.assertTrue((await (await self.client.get('/api/auth/config')).json())['enabled'])
        settings = await (await self.client.get('/api/settings')).json()
        self.assertTrue(settings['source_configured'])
        self.assertEqual(settings['storage_cap_bytes'], 90000000)
        self.assertEqual((await self.client.get('/api/status', headers={'Authorization':'Bearer '+operator['key']})).status, 200)

    async def test_invalid_values_never_change_saved_branding(self):
        for value in ['red', '#11223344', 'rgb(999, 0, 0)', 'url(evil)', '#12', {'r': -1, 'g': 2, 'b': 3}]:
            response = await self.client.put('/api/v1/branding', json={'light':{'text':value}})
            self.assertEqual(response.status, 400, value)
        for bad in ['', '<script>', ' x'*101]:
            self.assertEqual((await self.client.put('/api/v1/branding', json={'name':bad})).status, 400)
        self.assertEqual((await (await self.client.get('/api/v1/branding')).json())['name'], 'Stream Recorder')
        self.assertEqual((await (await self.client.get('/api/v1/branding')).json())['light']['text'], '#171717')

    async def test_image_assets_are_public_durable_and_reset_independently(self):
        def png(color):
            output = io.BytesIO()
            Image.new('RGB', (40, 30), color).save(output, format='PNG')
            return output.getvalue()

        original = png('red')
        newer = png('blue')
        path = '/api/v1/branding/assets/light_logo'
        self.assertEqual((await self.client.get(path)).status, 404)
        uploaded = await self.client.put(path, data=original, headers={'Content-Type': 'image/png'})
        self.assertEqual(uploaded.status, 200)
        first_url = (await uploaded.json())['assets']['light_logo']
        self.assertEqual((await (await self.client.get(first_url)).read()), original)
        self.assertEqual((await self.client.get(first_url)).headers['Content-Type'], 'image/png')
        changed = await self.client.put(path, data=newer, headers={'Content-Type': 'image/png'})
        self.assertEqual(changed.status, 200)
        self.assertNotEqual((await changed.json())['assets']['light_logo'], first_url)
        self.assertEqual((await (await self.client.get(path)).read()), newer)
        for slot in ('dark_logo', 'favicon'):
            response = await self.client.put(f'/api/v1/branding/assets/{slot}', data=original, headers={'Content-Type': 'image/png'})
            self.assertEqual(response.status, 200)
        await self.client.close()
        await self.open_app()
        self.assertEqual((await (await self.client.get(path)).read()), newer)
        self.assertEqual((await (await self.client.get('/api/v1/branding')).json())['assets']['dark_logo'].startswith('/api/v1/branding/assets/'), True)
        self.assertEqual((await self.client.delete(path)).status, 200)
        self.assertEqual((await self.client.get(path)).status, 404)
        self.assertEqual((await self.client.post('/api/v1/branding/reset', json={})).status, 200)
        self.assertEqual((await self.client.get('/api/v1/branding/assets/favicon')).status, 404)

    async def test_bad_uploads_preserve_prior_asset_and_admin_controls(self):
        output = io.BytesIO()
        Image.new('RGB', (20, 20), 'green').save(output, format='PNG')
        original = output.getvalue()
        path = '/api/v1/branding/assets/dark_logo'
        self.assertEqual((await self.client.put(path, data=original, headers={'Content-Type':'image/png'})).status, 200)
        for content_type, data in [('image/svg+xml', b'<svg onload="alert(1)"/>'), ('image/png', b'not a png'), ('image/png', original + b'<script>alert(1)</script>'), ('image/png', b'0' * (2 * 1024 * 1024 + 1))]:
            response = await self.client.put(path, data=data, headers={'Content-Type':content_type})
            self.assertIn(response.status, (400, 413), content_type)
            self.assertEqual((await (await self.client.get(path)).read()), original)
        self.assertEqual((await self.client.put('/api/v1/branding/assets/other', data=original, headers={'Content-Type':'image/png'})).status, 404)
        await self.client.post('/api/auth/config', json={'username':'admin', 'password':'correct horse battery staple', 'enabled':True})
        self.assertEqual((await self.client.get(path)).status, 200)
        self.assertEqual((await self.client.put(path, data=original, headers={'Content-Type':'image/png'})).status, 401)
        operator = await (await self.client.post('/api/auth/login', json={'username':'admin','password':'correct horse battery staple'})).json()
        self.assertEqual((await self.client.put(path, data=original, headers={'Content-Type':'image/png','X-CSRF-Token':operator['csrf_token']})).status, 200)
