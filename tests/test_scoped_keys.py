"""Access decisions at the public HTTP seam, including persistent upgrades."""
import json
import math
import sqlite3
import tempfile
import time
import unittest

from aiohttp.test_utils import TestClient, TestServer

from recorder.server import create_app


class ScopedKeyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        await self.reopen()

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def reopen(self):
        self.client = TestClient(TestServer(create_app(self.tmp.name)))
        await self.client.start_server()

    async def key(self, permissions, role='operator', **kwargs):
        response = await self.client.post('/api/v1/auth/keys', json={
            'label': 'calendar', 'role': role, 'permissions': permissions, **kwargs,
        })
        self.assertEqual(response.status, 201, await response.text())
        return await response.json()

    async def test_scope_controls_both_versions_and_denied_write_has_no_effect(self):
        permissions = await (await self.client.get('/api/v1/auth/permissions')).json()
        self.assertIn('recordings:title', permissions['operator'])
        self.assertIn('auth:manage', permissions['admin'])
        self.assertNotIn('auth:manage', permissions['operator'])
        await self.client.put('/api/settings', json={'source_url': 'https://example.org/live.m3u8'})
        key = await self.key(['recordings:read'])
        headers = {'Authorization': 'Bearer ' + key['key']}
        for prefix in ('/api', '/api/v1'):
            self.assertEqual((await self.client.get(prefix + '/settings', headers=headers)).status, 403)
            self.assertEqual((await self.client.post(prefix + '/recordings', json={}, headers=headers)).status, 403)
            self.assertEqual((await self.client.get(prefix + '/recordings', headers=headers)).status, 200)
        self.assertEqual((await self.client.get('/api/status')).status, 200)
        self.assertEqual((await self.client.get('/api/v1/branding', headers=headers)).status, 403)
        self.assertEqual((await self.client.get('/api/v1/recordings', headers={'Authorization': 'Bearer wrong'})).status, 401)

    async def test_branding_asset_routes_are_public_reads_and_admin_scoped_writes(self):
        for prefix in ('/api', '/api/v1'):
            self.assertEqual((await self.client.get(prefix+'/branding/assets/light_logo')).status, 404)
        reader = await self.key(['branding:read'])
        self.assertEqual((await self.client.delete('/api/v1/branding/assets/light_logo', headers={
            'Authorization':'Bearer '+reader['key'],
        })).status, 403)
        writer = await self.key(['branding:write'], role='admin')
        self.assertEqual((await self.client.delete('/api/branding/assets/light_logo', headers={
            'Authorization':'Bearer '+writer['key'],
        })).status, 200)

    async def test_expiry_revocation_and_last_use_survive_restart_without_readback(self):
        for timestamp in (float('nan'), float('inf'), time.time() - 1):
            rejected = await self.client.post('/api/v1/auth/keys', json={
                'label':'bad expiry','permissions':['status:read'],'expires_at':timestamp,
            })
            self.assertEqual(rejected.status, 400)
        key = await self.key(['status:read'], expires_at=time.time() + 3600)
        headers = {'Authorization': 'Bearer ' + key['key']}
        self.assertEqual((await self.client.get('/api/status', headers=headers)).status, 200)
        listed = await (await self.client.get('/api/auth/keys')).json()
        self.assertNotIn(key['key'], json.dumps(listed))
        self.assertIsInstance(listed[0]['last_used_at'], float)
        await self.client.close()
        await self.reopen()
        listed = await (await self.client.get('/api/v1/auth/keys')).json()
        self.assertIsInstance(listed[0]['last_used_at'], float)
        self.assertEqual((await self.client.get('/api/v1/status', headers=headers)).status, 200)
        self.assertEqual((await self.client.delete('/api/v1/auth/keys/' + key['id'])).status, 200)
        self.assertEqual((await self.client.get('/api/status', headers=headers)).status, 401)
        expired = await self.key(['status:read'], expires_at=time.time() + .1)
        await __import__('asyncio').sleep(.15)
        self.assertEqual((await self.client.get('/api/status', headers={'Authorization':'Bearer '+expired['key']})).status, 401)

    async def test_legacy_role_only_key_works_after_upgrade(self):
        key = await self.key(['recordings:read'])
        await self.client.close()
        with sqlite3.connect(self.tmp.name + '/state.sqlite') as db:
            state = json.loads(db.execute('SELECT payload FROM auth_state WHERE id=1').fetchone()[0])
            state['keys'][0] = {k: v for k,v in state['keys'][0].items() if k not in {'permissions','expires_at','last_used_at'}}
            db.execute('UPDATE auth_state SET payload=? WHERE id=1', (json.dumps(state),))
        await self.reopen()
        headers = {'Authorization':'Bearer '+key['key']}
        self.assertEqual((await self.client.get('/api/v1/recordings', headers=headers)).status, 200)
        self.assertEqual((await self.client.post('/api/stop', headers=headers)).status, 200)
        self.assertEqual((await self.client.get('/api/settings', headers=headers)).status, 403)

    async def test_required_key_keeps_browser_csrf_and_never_falls_back_from_bad_key(self):
        key = await self.key(['status:read'])
        await self.client.post('/api/auth/config', json={'username':'admin','password':'correct horse battery staple','enabled':True})
        login = await (await self.client.post('/api/auth/login', json={'username':'admin','password':'correct horse battery staple'})).json()
        csrf = {'X-CSRF-Token':login['csrf_token']}
        self.assertEqual((await self.client.post('/api/v1/auth/config', json={'require_api_key':True}, headers=csrf)).status, 200)
        self.assertEqual((await self.client.get('/api/v1/status')).status, 200)
        self.assertEqual((await self.client.get('/api/v1/status', headers={'Authorization':'Bearer invalid'})).status, 401)
        self.assertEqual((await self.client.post('/api/auth/config', json={'require_api_key':False})).status, 403)
        self.assertEqual((await self.client.post('/api/auth/config', json={'require_api_key':False}, headers=csrf)).status, 200)
        await self.client.post('/api/auth/config', json={'require_api_key':True}, headers=csrf)
        await self.client.post('/api/auth/logout', headers=csrf)
        self.assertEqual((await self.client.get('/api/status')).status, 401)
        self.assertEqual((await self.client.get('/api/status', headers={'Authorization':'Bearer '+key['key']})).status, 200)

    async def test_machine_key_protection_can_be_enabled_without_browser_auth(self):
        key = await self.key(['status:read'])
        response = await self.client.post('/api/auth/config', json={
            'username':'admin','password':'correct horse battery staple', 'require_api_key':True,
        })
        self.assertEqual(response.status, 200, await response.text())
        self.assertFalse((await response.json())['enabled'])
        await self.client.close()
        await self.reopen()
        status = await (await self.client.get('/api/auth/status')).json()
        self.assertTrue(status['enabled'])
        self.assertIsNone(status['role'])
        self.assertEqual((await self.client.get('/api/status')).status, 401)
        self.assertEqual((await self.client.get('/api/status', headers={'Authorization':'Bearer '+key['key']})).status, 200)
        signed_in = await self.client.post('/api/auth/login', json={
            'username':'admin','password':'correct horse battery staple',
        })
        self.assertEqual(signed_in.status, 200)
        self.assertEqual((await self.client.get('/api/status')).status, 200)
        csrf = (await signed_in.json())['csrf_token']
        self.assertEqual((await self.client.post('/api/auth/config', json={'require_api_key':False}, headers={'X-CSRF-Token':csrf})).status, 200)
        self.assertEqual((await self.client.get('/api/auth/status')).status, 200)
