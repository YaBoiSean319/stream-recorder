import tempfile, unittest
from aiohttp.test_utils import TestClient, TestServer
from recorder.server import create_app

class AuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_oidc_configuration_masks_and_retains_secret(self):
        config = {'issuer':'https://identity.example', 'client_id':'recorder', 'client_secret':'private-secret', 'public_url':'https://recorder.example', 'operator_groups':['operators'], 'admin_groups':['admins'], 'enabled':True}
        self.assertEqual((await self.client.post('/api/auth/oidc/config', json=config)).status, 200)
        response = await self.client.get('/api/auth/oidc/config')
        self.assertNotIn('private-secret', await response.text())
        self.assertEqual((await self.client.post('/api/auth/oidc/config', json={'group_claim':'roles'})).status, 200)
        saved = await (await self.client.get('/api/auth/oidc/config')).json()
        self.assertTrue(saved['client_secret_saved'])
        self.assertEqual(saved['group_claim'], 'roles')
        self.assertEqual((await self.client.get('/api/auth/oidc/callback?state=invalid&code=x')).status, 401)
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = TestClient(TestServer(create_app(self.tmp.name)))
        await self.client.start_server()
    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()
    async def test_enable_login_csrf_and_logout(self):
        self.assertEqual((await self.client.get('/api/status')).status, 200)
        self.assertEqual((await self.client.post('/api/auth/config', json={'enabled': True})).status, 400)
        response = await self.client.post('/api/auth/config', json={'username':'admin', 'password':'correct horse battery staple', 'enabled':True})
        self.assertEqual(response.status, 200)
        self.assertEqual((await self.client.get('/api/status')).status, 401)

    async def test_settings_reflect_enabled_authentication(self):
        await self.client.post('/api/auth/config', json={'username':'admin', 'password':'correct horse battery staple', 'enabled':True})
        await self.client.post('/api/auth/login', json={'username':'admin', 'password':'correct horse battery staple'})
        settings = await (await self.client.get('/api/settings')).json()
        self.assertTrue(settings['auth_enabled'])

    async def test_public_origin_rejects_invalid_port(self):
        for origin in ('https://recorder.example:invalid', 'https://recorder.example:99999'):
            response = await self.client.post('/api/auth/config', json={'public_url':origin})
            self.assertEqual(response.status, 400)
        config = await (await self.client.get('/api/auth/config')).json()
        self.assertEqual(config['public_url'], '')

    async def test_operator_key_permissions_revocation_and_persistence(self):
        response = await self.client.post('/api/auth/keys', json={'label':'automation'})
        self.assertEqual(response.status, 201)
        key = await response.json()
        self.assertEqual(key['role'], 'operator')
        self.assertNotIn(key['key'], str(await (await self.client.get('/api/auth/keys')).json()))
        await self.client.post('/api/auth/config', json={'username':'admin', 'password':'correct horse battery staple', 'enabled':True})
        headers = {'Authorization':'Bearer '+key['key']}
        self.assertEqual((await self.client.get('/api/status', headers=headers)).status, 200)
        self.assertEqual((await self.client.put('/api/settings', json={}, headers=headers)).status, 403)
        self.assertEqual((await self.client.get('/api/settings', headers=headers)).status, 403)
        self.assertEqual((await self.client.delete('/api/recordings/no-such', headers=headers)).status, 403)
        self.assertEqual((await self.client.post('/api/stop', headers=headers)).status, 200)
        await self.client.close()
        self.client = TestClient(TestServer(create_app(self.tmp.name)))
        await self.client.start_server()
        self.assertEqual((await self.client.get('/api/status', headers=headers)).status, 200)
        response = await self.client.post('/api/auth/login', json={'username':'admin','password':'correct horse battery staple'})
        csrf = {'X-CSRF-Token':(await response.json())['csrf_token']}
        self.assertEqual((await self.client.delete('/api/auth/keys/'+key['id'], headers=csrf)).status, 200)
        self.assertEqual((await self.client.get('/api/status', headers=headers)).status, 401)
        self.assertEqual((await self.client.post('/api/auth/login', json={'username':'admin', 'password':'wrong'})).status, 401)
        response = await self.client.post('/api/auth/login', json={'username':'admin', 'password':'correct horse battery staple'})
        self.assertEqual(response.status, 200)
        token = (await response.json())['csrf_token']
        self.assertEqual((await self.client.get('/api/status')).status, 200)
        self.assertEqual((await self.client.post('/api/stop')).status, 403)
        self.assertEqual((await self.client.post('/api/stop', headers={'X-CSRF-Token':token})).status, 200)
        self.assertEqual((await self.client.post('/api/auth/logout', headers={'X-CSRF-Token':token})).status, 200)
        self.assertEqual((await self.client.get('/api/status')).status, 401)
