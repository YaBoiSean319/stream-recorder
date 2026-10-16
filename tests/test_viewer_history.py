"""Public HTTP contract for durable viewer history and range statistics."""
import asyncio
import time
import unittest
from unittest.mock import patch
import tempfile
from aiohttp.test_utils import TestClient, TestServer
from recorder.server import create_app


class ViewerHistoryHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.interval = patch('recorder.viewers.SAMPLE_INTERVAL', 0.01)
        self.interval.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.client = TestClient(TestServer(create_app(self.tmp.name)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()
        self.interval.stop()

    async def configure(self):
        return await (await self.client.put('/api/v1/viewers/config', json={
            'player_url':'https://player.example/watch',
            'allowed_origin':'https://player.example',
            'public_tracking_url':'https://recorder.example',
            'timezone':'UTC','enabled':True
        })).json()

    async def query(self, start, end, **params):
        from urllib.parse import urlencode
        query = urlencode({'from':start,'to':end,**params})
        return await self.client.get('/api/v1/viewers/history?'+query)

    async def test_healthy_zero_samples_are_available_and_persist_after_replacement(self):
        await self.configure()
        start = time.time()-0.08
        await asyncio.sleep(0.06)
        end = time.time()
        response = await self.query(start,end)
        self.assertEqual(response.status,200)
        data=await response.json()
        self.assertGreater(data['coverage_percent'],30)
        self.assertEqual(data['statistics']['peak'],0)
        self.assertEqual(data['statistics']['average'],0)
        self.assertEqual(data['statistics']['viewer_hours'],0)
        known=[point for point in data['points'] if point['covered_seconds']]
        self.assertTrue(known)
        self.assertTrue(all(point['average']==0 for point in known))
        await self.client.close()
        self.client=TestClient(TestServer(create_app(self.tmp.name)))
        await self.client.start_server()
        stored=await self.query(start,end)
        self.assertGreater((await stored.json())['coverage_seconds'],0)

    async def test_active_count_viewer_hours_and_weighted_average_are_reported(self):
        config=await self.configure()
        headers={'Origin':'https://player.example','X-Tracking-Key':config['tracking_key']}
        start=time.time()-0.09
        for browser in ('browser-00000000000001','browser-00000000000002'):
            result=await self.client.post('/api/v1/viewers/report',headers=headers,json={'browser_id':browser,'tab_id':'tab-0000000000000001','playing':True})
            self.assertEqual(result.status,202)
        await asyncio.sleep(0.05)
        stopped=await self.client.post('/api/v1/viewers/report',headers=headers,json={'browser_id':'browser-00000000000002','tab_id':'tab-0000000000000001','playing':False})
        self.assertEqual(stopped.status,202)
        await asyncio.sleep(0.05)
        end=time.time()
        data=await (await self.query(start,end)).json()
        stats=data['statistics']
        self.assertEqual(stats['peak'],2)
        self.assertGreater(stats['average'],0)
        self.assertLess(stats['average'],2)
        self.assertGreater(stats['viewer_hours'],0)
        self.assertGreater(stats['covered_seconds'],0)
        self.assertEqual(data['live']['count'],1)

    async def test_disabled_and_partial_periods_remain_unknown_and_ranges_are_bounded(self):
        now=time.time()
        no_history=await (await self.query(now-10,now)).json()
        self.assertEqual(no_history['coverage_seconds'],0)
        self.assertIsNone(no_history['statistics']['average'])
        await self.configure()
        await asyncio.sleep(0.04)
        recent=time.time()
        partial=await (await self.query(recent-0.05,recent)).json()
        self.assertLess(partial['coverage_percent'],100)
        too_long=await self.query(time.time()-91*86400,time.time())
        self.assertEqual(too_long.status,400)
        bad=await self.query(time.time()-60,time.time(),resolution='5s')
        self.assertEqual(bad.status,400)

    async def test_history_requires_view_permission_for_scoped_api_keys(self):
        status_key=await (await self.client.post('/api/v1/auth/keys',json={'label':'status-only','permissions':['status:read']})).json()
        denied=await self.client.get('/api/v1/viewers/history?from=1&to=2',headers={'Authorization':'Bearer '+status_key['key']})
        self.assertEqual(denied.status,403)
        viewer_key=await (await self.client.post('/api/v1/auth/keys',json={'label':'analytics','role':'operator','permissions':['viewers:read']})).json()
        allowed=await self.client.get('/api/v1/viewers/history?from='+str(time.time()-60)+'&to='+str(time.time()),headers={'Authorization':'Bearer '+viewer_key['key']})
        self.assertEqual(allowed.status,200)
        manage_denied=await self.client.put('/api/v1/viewers/config',json={'enabled':False},headers={'Authorization':'Bearer '+viewer_key['key']})
        self.assertEqual(manage_denied.status,403)

    async def test_player_changes_create_separate_queryable_historical_configurations(self):
        await self.configure()
        await asyncio.sleep(0.04)
        start=time.time()-0.08
        initial=await (await self.query(start,time.time())).json()
        await self.client.put('/api/v1/viewers/config',json={'player_url':'https://other.example/watch','allowed_origin':'https://other.example'})
        await asyncio.sleep(0.04)
        end=time.time()
        current=await (await self.query(start,end)).json()
        self.assertNotEqual(current['configuration_id'],initial['configuration_id'])
        self.assertGreaterEqual(len(current['configurations']),2)
        old=await (await self.query(start,end,configuration_id=initial['configuration_id'])).json()
        self.assertNotEqual(old['configuration_id'],current['configuration_id'])
        self.assertGreater(old['coverage_seconds'],0)
