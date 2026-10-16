"""Public HTTP contract for durable viewer history and range statistics."""
import asyncio
import datetime as dt
import json
import time
import unittest
from unittest.mock import patch
import tempfile
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from recorder.server import Recorder, create_app


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
        too_long=await self.query(time.time()-731*86400,time.time())
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

    async def test_retention_defaults_are_exposed_and_invalid_policy_is_rejected_atomically(self):
        response=await self.client.get('/api/v1/viewers/config')
        config=await response.json()
        self.assertEqual(config['detail_retention_days'],90)
        self.assertEqual(config['history_retention_days'],730)
        bad=await self.client.put('/api/v1/viewers/config',json={
            'detail_retention_days':90,'history_retention_days':30
        })
        self.assertEqual(bad.status,400)
        after=await (await self.client.get('/api/v1/viewers/config')).json()
        self.assertEqual(after['detail_retention_days'],90)
        self.assertEqual(after['history_retention_days'],730)

    async def test_old_samples_are_rolled_up_once_and_survive_process_replacement(self):
        await self.configure()
        saved=await self.client.put('/api/v1/viewers/config',json={
            'detail_retention_days':1,'history_retention_days':3
        })
        self.assertEqual(saved.status,200)
        viewer=next(value for value in self.client.server.app._state.values() if isinstance(value,Recorder))
        config=json.loads(viewer.db.execute('SELECT payload FROM viewer_config WHERE id=1').fetchone()[0])
        identity=config['configuration_id']
        hour=int((time.time()-2*86400)//3600*3600)
        with viewer.db:
            for offset,count in ((0,1),(30,3),(60,2),(90,0)):
                viewer.db.execute('INSERT OR REPLACE INTO viewer_samples VALUES(?,?,?,1)',(hour+offset,count,identity))
        await asyncio.sleep(.05)
        response=await self.query(hour,hour+3600,resolution='30s')
        self.assertEqual(response.status,200)
        data=await response.json()
        self.assertEqual(data['resolution_seconds'],3600)
        self.assertEqual(data['statistics']['peak'],3)
        self.assertAlmostEqual(data['statistics']['average'],1.5,places=2)
        self.assertAlmostEqual(data['statistics']['viewer_hours'],.06/3600,places=8)
        self.assertAlmostEqual(data['coverage_seconds'],.04,places=4)
        self.assertEqual(data['precision'],'hourly-rollup')
        day_key=dt.datetime.fromtimestamp(hour,dt.timezone.utc).date().isoformat()
        rolled_day=next(day for day in data['daily_peaks'] if day['date']==day_key)
        self.assertEqual(rolled_day['peak'],3)
        self.assertIsNone(rolled_day['peak_at'])
        self.assertAlmostEqual(rolled_day['covered_seconds'],.04,places=4)
        partial=await (await self.query(hour+60,hour+3600,resolution='30s')).json()
        self.assertEqual(partial['partial_rollup_hours_excluded'],1)
        self.assertEqual(partial['coverage_seconds'],0)
        self.assertTrue(partial['detail_expired'])
        self.assertAlmostEqual(data['points'][0]['covered_seconds'],.04,places=4)
        await self.client.close()
        self.client=TestClient(TestServer(create_app(self.tmp.name)))
        await self.client.start_server()
        restored=await (await self.query(hour,hour+3600)).json()
        self.assertAlmostEqual(restored['coverage_seconds'],.04,places=4)
        self.assertAlmostEqual(restored['statistics']['average'],1.5,places=2)
        restored_service=next(value for value in self.client.server.app._state.values() if isinstance(value,Recorder))
        expired_hour=int((time.time()-4*86400)//3600*3600)
        with restored_service.db:
            restored_service.db.execute('INSERT OR REPLACE INTO viewer_samples VALUES(?,?,?,1)',(expired_hour,9,identity))
        await asyncio.sleep(.04)
        expired=await (await self.query(expired_hour,expired_hour+3600)).json()
        self.assertTrue(expired['history_expired'])
        self.assertEqual(expired['coverage_seconds'],0)

    async def test_timezone_updates_preserve_player_identity_and_stale_keys_cannot_report(self):
        first=await self.configure()
        before=(await (await self.client.get('/api/v1/viewers/config')).json())['configuration_id']
        update=await self.client.put('/api/v1/viewers/config',json={'timezone':'America/Indiana/Indianapolis'})
        self.assertEqual(update.status,200)
        after=(await (await self.client.get('/api/v1/viewers/config')).json())['configuration_id']
        self.assertEqual(before,after)
        await self.client.put('/api/v1/viewers/config',json={'player_url':'https://other.example/watch','allowed_origin':'https://other.example'})
        stale=await self.client.post('/api/v1/viewers/report',headers={'Origin':'https://player.example','X-Tracking-Key':first['tracking_key']},json={'browser_id':'browser-00000000000001','tab_id':'tab-0000000000000001','playing':True})
        self.assertEqual(stale.status,401)
