"""Versioned HTTP contract, exercised without internal database inspection."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from aiohttp.test_utils import TestClient, TestServer

from recorder.server import create_app


class VersionedApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        await self.open_client()

    async def open_client(self):
        self.client = TestClient(TestServer(create_app(self.tmp.name)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def test_compatibility_pagination_and_stable_errors(self):
        legacy = await (await self.client.get('/api/status')).json()
        self.assertEqual(await (await self.client.get('/api/v1/status')).json(), legacy)
        self.assertEqual((await self.client.get('/api/v1/health')).status, 200)
        page = await (await self.client.get('/api/v1/recordings?limit=1&offset=0')).json()
        self.assertEqual(page, {'items': [], 'total': 0, 'limit': 1, 'offset': 0, 'next_offset': None})
        self.assertEqual(await (await self.client.get('/api/recordings')).json(), [])
        invalid = await self.client.get('/api/v1/recordings?limit=0')
        self.assertEqual(invalid.status, 400)
        self.assertEqual((await invalid.json())['error']['code'], 'invalid_request')
        absent = await self.client.get('/api/v1/recordings/missing')
        self.assertEqual(absent.status, 404)
        self.assertEqual((await absent.json())['error']['code'], 'not_found')

    async def test_idempotent_admission_race_and_restart(self):
        await self.client.put('/api/v1/settings', json={'source_url': 'http://127.0.0.1:9/live.m3u8'})
        headers = {'Idempotency-Key': 'calendar-event-42'}
        async def start():
            response = await self.client.post('/api/v1/recordings', json={'duration_seconds': 30}, headers=headers)
            return response.status, await response.json()
        first, replay = await asyncio.gather(start(), start())
        self.assertEqual(first[0], 201)
        self.assertEqual(replay[0], 201)
        self.assertEqual(first[1]['id'], replay[1]['id'])
        changed = await self.client.post('/api/v1/recordings', json={'duration_seconds': 20}, headers=headers)
        self.assertEqual(changed.status, 409)
        self.assertEqual((await changed.json())['error']['code'], 'idempotency_conflict')
        await self.client.close()
        await self.open_client()
        repeated = await self.client.post('/api/v1/recordings', json={'duration_seconds': 30}, headers=headers)
        self.assertEqual((await repeated.json())['id'], first[1]['id'])

    async def test_scoped_key_expiry_and_versioned_auth(self):
        response = await self.client.post('/api/v1/auth/keys', json={'label': 'status', 'permissions': ['status:read']})
        self.assertEqual(response.status, 201)
        key = await response.json()
        auth = {'Authorization': 'Bearer ' + key['key']}
        self.assertEqual((await self.client.get('/api/v1/status', headers=auth)).status, 200)
        self.assertEqual((await self.client.get('/api/v1/recordings', headers=auth)).status, 403)
        listed = await (await self.client.get('/api/v1/auth/keys')).json()
        self.assertNotIn(key['key'], str(listed))
        self.assertIsNotNone(listed[0]['last_used_at'])
        expired = await self.client.post('/api/v1/auth/keys', json={'label': 'expired', 'expires_at': 1})
        self.assertEqual(expired.status, 400)

    async def test_title_and_pin_scopes_check_each_field(self):
        await self.client.put('/api/v1/settings', json={'source_url':'http://127.0.0.1:9/live.m3u8'})
        recording = await (await self.client.post('/api/v1/recordings', json={'title':'First'})).json()
        title_key = await (await self.client.post('/api/v1/auth/keys', json={
            'label':'editor','permissions':['recordings:title'],
        })).json()
        pin_key = await (await self.client.post('/api/v1/auth/keys', json={
            'label':'pin','permissions':['recordings:pin'],
        })).json()
        path = '/api/v1/recordings/' + recording['id']
        editor = {'Authorization':'Bearer '+title_key['key']}
        pin = {'Authorization':'Bearer '+pin_key['key']}
        self.assertEqual((await self.client.patch(path, json={'title':'Second'}, headers=editor)).status, 200)
        self.assertEqual((await self.client.patch(path, json={'pinned':True}, headers=editor)).status, 403)
        self.assertEqual((await self.client.patch(path, json={'pinned':True}, headers=pin)).status, 200)
        self.assertEqual((await self.client.patch(path, json={'title':'Third'}, headers=pin)).status, 403)
        self.assertEqual((await self.client.patch(path, json={'title':'Third','pinned':False}, headers=editor)).status, 403)

    async def test_protected_preview_and_download_permissions(self):
        await self.client.post('/api/auth/config', json={
            'username': 'admin', 'password': 'correct horse battery staple', 'enabled': True,
        })
        self.assertEqual((await self.client.get('/api/v1/preview')).status, 401)
        login = await self.client.post('/api/v1/auth/login', json={
            'username': 'admin', 'password': 'correct horse battery staple',
        })
        self.assertEqual(login.status, 200)
        self.assertEqual((await self.client.get('/api/v1/preview')).status, 200)

    async def test_key_requirement_preserves_browser_recovery(self):
        response = await self.client.post('/api/v1/auth/config', json={'require_api_key': True})
        self.assertEqual(response.status, 400)
        key = await (await self.client.post('/api/v1/auth/keys', json={'label':'machine'})).json()
        await self.client.post('/api/v1/auth/config', json={
            'username':'admin', 'password':'correct horse battery staple', 'enabled':True,
        })
        login = await self.client.post('/api/v1/auth/login', json={
            'username':'admin', 'password':'correct horse battery staple',
        })
        csrf = (await login.json())['csrf_token']
        response = await self.client.post('/api/v1/auth/config', json={'require_api_key':True}, headers={'X-CSRF-Token':csrf})
        self.assertEqual(response.status, 200)
        self.assertEqual((await self.client.get('/api/v1/status')).status, 200)
        await self.client.post('/api/v1/auth/logout', headers={'X-CSRF-Token':csrf})
        self.assertEqual((await self.client.get('/api/v1/status')).status, 401)
        self.assertEqual((await self.client.get('/api/v1/status', headers={'Authorization':'Bearer '+key['key']})).status, 200)

    async def test_checked_contract_and_docs_under_access(self):
        response = await self.client.get('/api/v1/openapi.json')
        self.assertEqual(response.status, 200)
        document = await response.json()
        saved = json.loads((Path(__file__).parent.parent/'docs'/'openapi.json').read_text())
        self.assertEqual(document, saved)
        for path, verb in [('/api/v1/status','get'),('/api/v1/recordings','post'),
                           ('/api/v1/recordings/{id}/download','get'),('/api/v1/auth/keys','post')]:
            self.assertIn(verb, document['paths'][path])
            self.assertIn('x-permission', document['paths'][path][verb])
        self.assertEqual((await self.client.get('/api/v1/docs')).status, 200)
        await self.client.post('/api/v1/auth/config', json={
            'username':'admin', 'password':'correct horse battery staple', 'enabled':True,
        })
        self.assertEqual((await self.client.get('/api/v1/docs')).status, 401)


if __name__ == '__main__':
    unittest.main()
