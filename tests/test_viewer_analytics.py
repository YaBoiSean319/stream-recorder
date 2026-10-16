"""HTTP contract for local-time audience activity and recording context."""
import datetime as dt
import csv
import io
import tempfile
import time
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from aiohttp.test_utils import TestClient, TestServer
from recorder.server import Recorder, create_app
from recorder.viewers import Viewers


class ViewerAnalyticsHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(self.tmp.name)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        await self.client.put('/api/v1/viewers/config', json={
            'player_url':'https://player.example/watch', 'allowed_origin':'https://player.example',
            'public_tracking_url':'https://recorder.example', 'timezone':'America/Indiana/Indianapolis', 'enabled':True
        })

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def test_daily_peaks_heatmap_periods_and_recordings_use_reporting_timezone(self):
        zone = ZoneInfo('America/Indiana/Indianapolis')
        # These known sample intervals span the 2026 spring DST transition.
        local = [dt.datetime(2026, 3, 8, 1, 30, tzinfo=zone),
                 dt.datetime(2026, 3, 8, 3, 30, tzinfo=zone),
                 dt.datetime(2026, 3, 9, 3, 30, tzinfo=zone),
                 dt.datetime(2026, 3, 15, 3, 30, tzinfo=zone)]
        viewers = next(value for value in self.app._state.values() if isinstance(value, Viewers))
        for stamp, count in zip(local, (2, 5, 3, 4)):
            await viewers.sample_once(stamp.timestamp())
            viewers.db.execute('UPDATE viewer_samples SET count=? WHERE sampled_at=?', (count, stamp.timestamp()))
        viewers.db.commit()
        recorder = next(value for value in self.app._state.values() if isinstance(value, Recorder))
        recorder.records['fixture-recording'] = {
            'id':'fixture-recording', 'state':'ready', 'started_at':local[0].isoformat(),
            'ended_at':local[1].isoformat(), 'title':'Sunday service', 'interruptions':[]
        }
        from_ts = dt.datetime(2026, 3, 8, 0, tzinfo=zone).timestamp()
        to_ts = dt.datetime(2026, 3, 16, 0, tzinfo=zone).timestamp()
        with patch.object(viewers, 'parse_range', return_value=(from_ts, to_ts)):
            response = await self.client.get(f'/api/v1/viewers/history?from={from_ts}&to={to_ts}&resolution=30s')
        self.assertEqual(response.status, 200)
        data = await response.json()
        with patch.object(viewers, 'parse_range', return_value=(from_ts, to_ts)):
            csv_response = await self.client.get(f"/api/v1/viewers/export.csv?from={from_ts}&to={to_ts}&resolution=30s&timezone=America%2FIndiana%2FIndianapolis&configuration_id={data['configuration_id']}")
        daily_row = next(row for row in csv.DictReader(io.StringIO(await csv_response.text())) if row['row_type'] == 'daily_peak' and row['name'] == '2026-03-08')
        self.assertEqual(float(daily_row['expected_seconds']), 23 * 3600, 'CSV preserves spring DST day length and selected configuration')
        self.assertEqual(data['timezone'], 'America/Indiana/Indianapolis')
        sun = next(day for day in data['daily_peaks'] if day['date'] == '2026-03-08')
        self.assertEqual(sun['peak'], 5)
        self.assertEqual(sun['unit'], 'browsers')
        self.assertEqual(sun['expected_seconds'], 23 * 3600, 'Spring DST day has 23 local hours')
        cell = next(cell for cell in data['heatmap'] if cell['day_of_week'] == 0 and cell['hour'] == 3)
        self.assertEqual(cell['peak'], 5)
        self.assertEqual([period['date'] for period in cell['periods']], ['2026-03-08','2026-03-15'])
        recording = data['recordings'][0]
        self.assertEqual(recording['id'], 'fixture-recording')
        self.assertEqual(recording['end'], local[1].timestamp())

    async def test_fall_back_hour_keeps_both_real_periods_separate(self):
        zone = ZoneInfo('America/Indiana/Indianapolis')
        first = dt.datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0)
        second = dt.datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1)
        viewers = next(value for value in self.app._state.values() if isinstance(value, Viewers))
        for stamp, count in ((first.timestamp(), 2), (second.timestamp(), 4)):
            await viewers.sample_once(stamp)
            viewers.db.execute('UPDATE viewer_samples SET count=? WHERE sampled_at=?', (count, stamp))
        viewers.db.commit()
        start = dt.datetime(2026, 11, 1, 0, tzinfo=zone).timestamp()
        end = dt.datetime(2026, 11, 2, 0, tzinfo=zone).timestamp()
        with patch.object(viewers, 'parse_range', return_value=(start, end)):
            response = await self.client.get(f'/api/v1/viewers/history?from={start}&to={end}')
        data = await response.json()
        repeated = next(cell for cell in data['heatmap'] if cell['day_of_week'] == 0 and cell['hour'] == 1)
        self.assertEqual([period['utc_offset_seconds'] for period in repeated['periods']], [-14400,-18000])
        self.assertEqual(repeated['expected_seconds'], 2 * 3600)

    async def test_active_recording_uses_now_and_read_authorization_is_preserved(self):
        now = dt.datetime.now(dt.timezone.utc)
        next(value for value in self.app._state.values() if isinstance(value, Recorder)).records['active-fixture'] = {
            'id':'active-fixture', 'state':'reconnecting', 'started_at':(now-dt.timedelta(minutes=2)).isoformat(),
            'interruptions':[{'at':now.isoformat(), 'reason':'fixture'}]
        }
        key = await (await self.client.post('/api/v1/auth/keys', json={
            'label':'analytics-read', 'role':'operator', 'permissions':['viewers:read']
        })).json()
        start, end = (now-dt.timedelta(minutes=3)).timestamp(), now.timestamp()
        url = f'/api/v1/viewers/history?from={start}&to={end}'
        denied = await self.client.get(url, headers={'Authorization':'Bearer invalid'})
        self.assertIn(denied.status, (401, 403))
        allowed = await self.client.get(url, headers={'Authorization':'Bearer '+key['key']})
        self.assertEqual(allowed.status, 200)
        recording = (await allowed.json())['recordings'][0]
        self.assertEqual(recording['end'], (await allowed.json())['to'])
        self.assertEqual(recording['state'], 'reconnecting')

    async def test_csv_export_matches_history_range_and_has_explicit_units_coverage_and_no_ids(self):
        now = time.time() - 7200
        viewers = next(value for value in self.app._state.values() if isinstance(value, Viewers))
        await viewers.sample_once(now - 60)
        viewers.db.execute('UPDATE viewer_samples SET count=7 WHERE sampled_at=?', (now - 60,))
        viewers.db.commit()
        recorder = next(value for value in self.app._state.values() if isinstance(value, Recorder))
        recorder.records['csv-fixture'] = {'id':'csv-fixture','state':'ready','started_at':dt.datetime.fromtimestamp(now-55,dt.timezone.utc).isoformat(),'ended_at':dt.datetime.fromtimestamp(now-50,dt.timezone.utc).isoformat(),'title':'=HYPERLINK("https://bad.example")','interruptions':[]}
        start, end = now - 90, now - 30
        query = f'from={start}&to={end}&resolution=30s&timezone=UTC'
        with patch.object(viewers, 'parse_range', return_value=(start, end)):
            history = await (await self.client.get('/api/v1/viewers/history?' + query)).json()
            response = await self.client.get('/api/v1/viewers/export.csv?' + query)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers['Content-Type'].split(';')[0], 'text/csv')
        self.assertIn('attachment; filename="viewer-analytics.csv"', response.headers['Content-Disposition'])
        text = await response.text()
        self.assertIn('meta,timezone,UTC', text)
        self.assertIn('meta,unit,browsers', text)
        rows = list(csv.DictReader(io.StringIO(text)))
        peak = next(row for row in rows if row['row_type'] == 'statistic' and row['name'] == 'peak')
        average = next(row for row in rows if row['row_type'] == 'statistic' and row['name'] == 'average')
        self.assertEqual(float(peak['peak']), history['statistics']['peak'])
        self.assertEqual(float(average['average']), history['statistics']['average'])
        self.assertEqual(float(peak['covered_seconds']), history['coverage_seconds'])
        self.assertIn('live_status,current', text)
        recording = next(row for row in rows if row['row_type'] == 'recording')
        self.assertTrue(recording['name'].startswith("'=HYPERLINK"))
        self.assertTrue(any(row['row_type']=='point' and row['average']=='' and row['covered_seconds']=='0' and float(row['expected_seconds'])>0 for row in rows), 'An unknown gap remains blank rather than becoming a measured zero')
        self.assertNotIn('browser_id', text)
        self.assertNotIn('tab_id', text)
        self.assertNotIn('tracking_key', text)
        self.assertIn('point,', text)

    async def test_export_requires_export_scope_and_invalid_key_never_falls_back(self):
        key = await (await self.client.post('/api/v1/auth/keys', json={
            'label':'analytics-read-only', 'role':'operator', 'permissions':['viewers:read']
        })).json()
        query = f'from={time.time()-60}&to={time.time()}'
        denied = await self.client.get('/api/v1/viewers/export.csv?' + query, headers={'Authorization':'Bearer '+key['key']})
        self.assertEqual(denied.status, 403)
        export_key = await (await self.client.post('/api/v1/auth/keys', json={
            'label':'analytics-export', 'role':'operator', 'permissions':['viewers:export']
        })).json()
        allowed = await self.client.get('/api/v1/viewers/export.csv?' + query, headers={'Authorization':'Bearer '+export_key['key']})
        self.assertEqual(allowed.status, 200)
        too_long = await self.client.get(f"/api/v1/viewers/export.csv?from={time.time()-731*86400}&to={time.time()}", headers={'Authorization':'Bearer '+export_key['key']})
        self.assertEqual(too_long.status,400)
        invalid = await self.client.get('/api/v1/viewers/export.csv?' + query, headers={'Authorization':'Bearer invalid'})
        self.assertIn(invalid.status, (401,403))

    async def test_csv_export_matches_hourly_rollup_and_selected_configuration(self):
        config = await (await self.client.get('/api/v1/viewers/config')).json()
        await self.client.put('/api/v1/viewers/config', json={'detail_retention_days':1,'history_retention_days':3})
        viewers = next(value for value in self.app._state.values() if isinstance(value, Viewers))
        hour = int((time.time()-2*86400)//3600*3600)
        with viewers.db:
            viewers.db.execute('INSERT OR REPLACE INTO viewer_rollups VALUES(?,?,?,?,?)',(hour,config['configuration_id'],7200.0,3600.0,4))
        query = f"from={hour}&to={hour+3600}&resolution=30s&timezone=UTC&configuration_id={config['configuration_id']}"
        history = await (await self.client.get('/api/v1/viewers/history?'+query)).json()
        response = await self.client.get('/api/v1/viewers/export.csv?'+query)
        self.assertEqual(response.status,200)
        rows = list(csv.DictReader(io.StringIO(await response.text())))
        meta = {row['name']:row['timestamp_unix_seconds'] for row in rows if row['row_type']=='meta'}
        average = next(row for row in rows if row['row_type']=='statistic' and row['name']=='average')
        peak = next(row for row in rows if row['row_type']=='statistic' and row['name']=='peak')
        self.assertEqual(meta['configuration_id'],config['configuration_id'])
        self.assertEqual(meta['precision'],'hourly-rollup')
        self.assertEqual(float(average['average']),history['statistics']['average'])
        self.assertEqual(float(peak['peak']),history['statistics']['peak'])

    async def test_configuration_history_is_read_scoped_diagnostics_and_revocation_are_admin_scoped(self):
        viewer_key = await (await self.client.post('/api/v1/auth/keys', json={
            'label':'analytics-read', 'role':'operator', 'permissions':['viewers:read']
        })).json()
        headers = {'Authorization':'Bearer '+viewer_key['key']}
        configurations = await self.client.get('/api/v1/viewers/configurations', headers=headers)
        self.assertEqual(configurations.status, 200)
        self.assertGreaterEqual(len((await configurations.json())['items']), 1)
        self.assertEqual((await self.client.get('/api/v1/viewers/diagnostics', headers=headers)).status, 403)
        self.assertEqual((await self.client.delete('/api/v1/viewers/key', headers=headers)).status, 403)
        before = await (await self.client.get('/api/v1/viewers/config')).json()
        revoked = await self.client.delete('/api/v1/viewers/key')
        self.assertEqual(revoked.status, 200)
        self.assertFalse((await revoked.json())['tracking_key_configured'])
        after = await (await self.client.get('/api/v1/viewers/diagnostics')).json()
        self.assertFalse(after['tracking_key_configured'])
        self.assertNotIn('tracking_hash', str(after))
        readback = await (await self.client.get('/api/v1/viewers/config')).json()
        self.assertNotIn('tracking_key', readback)
        self.assertEqual(before['configuration_id'], readback['configuration_id'])
