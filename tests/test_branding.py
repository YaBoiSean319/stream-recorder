"""Shared branding is observable through public/versioned HTTP, across restarts."""
import tempfile
import unittest
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
