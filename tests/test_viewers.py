"""External-player tracking contract at public HTTP seam."""
import asyncio, tempfile, unittest
from aiohttp.test_utils import TestClient, TestServer
from recorder.server import create_app

class ViewerTrackingHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.client=TestClient(TestServer(create_app(self.tmp.name))); await self.client.start_server()
    async def asyncTearDown(self):
        await self.client.close(); self.tmp.cleanup()
    async def test_empty_independent_configuration_and_snippet(self):
        defaults=await (await self.client.get('/api/v1/viewers/config')).json()
        self.assertEqual(defaults['player_url'],''); self.assertFalse(defaults['enabled'])
        await self.client.put('/api/v1/settings',json={'source_url':'https://stream.example/live.m3u8'})
        r=await self.client.put('/api/v1/viewers/config',json={'player_url':'https://player.example/watch','allowed_origin':'https://player.example','public_tracking_url':'https://recorder.example','timezone':'America/Indiana/Indianapolis','enabled':True})
        self.assertEqual(r.status,200); data=await r.json(); self.assertTrue(data['tracking_key_configured']); self.assertIn('/api/v1/viewers/report',data['snippet'])
        read=await self.client.get('/api/v1/viewers/config'); self.assertNotIn(data['tracking_key'],(await read.text()))
        report=await self.client.post('/api/v1/viewers/report',json={'browser_id':'browser-id-123456','tab_id':'tab-id-123456789','playing':True},headers={'Origin':'https://player.example','X-Tracking-Key':data['tracking_key']})
        self.assertEqual(report.status,202)
        await self.client.close()
        self.client=TestClient(TestServer(create_app(self.tmp.name))); await self.client.start_server()
        self.assertTrue((await (await self.client.get('/api/v1/settings')).json())['source_configured'])
        self.assertNotIn(data['tracking_key'],await (await self.client.get('/api/v1/viewers/config')).text())
        self.assertEqual((await (await self.client.get('/api/v1/viewers/live')).json())['count'],1)
    async def test_deduplicated_tabs_stop_only_their_own_presence_and_validate_origin_key(self):
        config=await (await self.client.put('/api/v1/viewers/config',json={'player_url':'https://player.example/watch','allowed_origin':'https://player.example','public_tracking_url':'https://recorder.example','timezone':'UTC','enabled':True})).json()
        base={'Origin':'https://player.example'}; payload={'browser_id':'b-1234567890123456','tab_id':'t-1234567890123456','playing':True}
        bad=await self.client.post('/api/v1/viewers/report',json=payload,headers={**base,'X-Tracking-Key':'bad'}); self.assertEqual(bad.status,401)
        bad=await self.client.post('/api/v1/viewers/report',json=payload,headers={'Origin':'https://evil.example','X-Tracking-Key':config['tracking_key']}); self.assertEqual(bad.status,403)
        bad=await self.client.post('/api/v1/viewers/report',json={**payload,'email':'viewer@example.test'},headers={**base,'X-Tracking-Key':config['tracking_key']}); self.assertEqual(bad.status,400)
        for tab in ('t-1234567890123456','t-2234567890123456'):
            r=await self.client.post('/api/v1/viewers/report',json={**payload,'tab_id':tab},headers={**base,'X-Tracking-Key':config['tracking_key']}); self.assertEqual(r.status,202)
        self.assertEqual((await (await self.client.get('/api/v1/viewers/live')).json())['count'],1)
        await self.client.post('/api/v1/viewers/report',json={**payload,'playing':False},headers={**base,'X-Tracking-Key':config['tracking_key']})
        self.assertEqual((await (await self.client.get('/api/v1/viewers/live')).json())['count'],1)
    async def test_scoped_roles_and_key_rotation(self):
        config=await (await self.client.put('/api/v1/viewers/config',json={'player_url':'https://player.example/watch','allowed_origin':'https://player.example','public_tracking_url':'https://recorder.example','timezone':'UTC','enabled':True})).json()
        status=await (await self.client.post('/api/v1/auth/keys',json={'label':'status','permissions':['status:read']})).json()
        self.assertEqual((await self.client.get('/api/v1/viewers/live',headers={'Authorization':'Bearer '+status['key']})).status,403)
        op=await (await self.client.post('/api/v1/auth/keys',json={'label':'viewer','role':'operator','permissions':['viewers:read']})).json()
        self.assertEqual((await self.client.get('/api/v1/viewers/live',headers={'Authorization':'Bearer '+op['key']})).status,200)
        rotated=await self.client.post('/api/v1/viewers/key/rotate'); self.assertEqual(rotated.status,200)
        payload={'browser_id':'b-1234567890123456','tab_id':'t-1234567890123456','playing':True}
        old=await self.client.post('/api/v1/viewers/report',json=payload,headers={'Origin':'https://player.example','X-Tracking-Key':config['tracking_key']}); self.assertEqual(old.status,401)
        denied=await self.client.post('/api/v1/viewers/key/rotate',json={},headers={'Authorization':'Bearer '+op['key']})
        self.assertEqual(denied.status,403)
    async def test_disabled_collection_is_distinct_from_healthy_zero(self):
        disabled=await (await self.client.get('/api/v1/viewers/live')).json()
        self.assertEqual(disabled['state'],'disabled'); self.assertIsNone(disabled['count'])
        invalid=await self.client.put('/api/v1/viewers/config',json={'enabled':True})
        self.assertEqual(invalid.status,400)
        await self.client.put('/api/v1/viewers/config',json={'player_url':'https://player.example/watch','allowed_origin':'https://player.example','public_tracking_url':'https://recorder.example','timezone':'UTC','enabled':True})
        live=await (await self.client.get('/api/v1/viewers/live')).json()
        self.assertEqual(live['state'],'available'); self.assertEqual(live['count'],0)
    async def test_inactive_browser_presence_expires_after_ninety_seconds(self):
        config=await (await self.client.put('/api/v1/viewers/config',json={'player_url':'https://player.example/watch','allowed_origin':'https://player.example','public_tracking_url':'https://recorder.example','timezone':'UTC','enabled':True})).json()
        payload={'browser_id':'browser-id-123456','tab_id':'tab-id-123456789','playing':True}
        response=await self.client.post('/api/v1/viewers/report',json=payload,headers={'Origin':'https://player.example','X-Tracking-Key':config['tracking_key']})
        self.assertEqual(response.status,202)
        await asyncio.sleep(90.2)
        live=await (await self.client.get('/api/v1/viewers/live')).json()
        self.assertEqual(live['count'],0)
