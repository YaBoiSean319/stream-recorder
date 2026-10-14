import tempfile, unittest
from aiohttp.test_utils import TestClient, TestServer
from recorder.server import create_app

class AuthTests(unittest.IsolatedAsyncioTestCase):
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
