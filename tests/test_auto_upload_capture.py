"""Automatic upload eligibility at the public HLS recording boundary."""
import json
import sqlite3
import unittest
from tests import test_acceptance as capture_fixture


class AutoCaptureTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=capture_fixture.RecorderAcceptance.asyncSetUp
    asyncTearDown=capture_fixture.RecorderAcceptance.asyncTearDown
    start_server=capture_fixture.RecorderAcceptance.start_server
    kill_server=capture_fixture.RecorderAcceptance.kill_server
    request=capture_fixture.RecorderAcceptance.request
    wait_terminal=capture_fixture.RecorderAcceptance.wait_terminal
    capture=capture_fixture.RecorderAcceptance.capture
    async def test_clean_future_recording_snapshots_defaults_and_survives_restart(self):
        await self.kill_server()
        with sqlite3.connect(self.root/'data'/'state.sqlite') as db:
            db.execute('INSERT OR REPLACE INTO youtube_connection VALUES(1,?)', (json.dumps({
                'client_id':'fixture','client_secret':'fixture','channel':{'id':'UCfixture','name':'Fixture'},
                'token':{'access_token':'fixture','expires_at':9999999999},'reconnect_required':False}),))
        await self.start_server()
        _, before = await self.request('GET','/api/v1/youtube/automation')
        self.assertFalse(before['enabled'])
        first=await self.capture(2)
        await self.request('PUT','/api/v1/youtube/automation',{'enabled':True,'made_for_kids':False,
            'title_template':'{recording_title} {source_name} {start_date}', 'description_template':'at {start_time}'})
        self.assertEqual((await self.request('GET',f"/api/v1/recordings/{first['id']}/uploads"))[1]['items'],[])
        _, started=await self.request('POST','/api/v1/recordings',{'duration_seconds':2,'title':'Morning','upload_metadata':{'tags':['calendar']}})
        ready=await self.wait_terminal(started['id'])
        self.assertEqual(ready['state'],'ready',ready)
        _, result=await self.request('GET',f"/api/v1/recordings/{ready['id']}/uploads")
        self.assertEqual(len(result['items']),1)
        metadata=result['items'][0]['metadata']
        self.assertIn('Morning Stream ',metadata['title'])
        self.assertEqual(metadata['tags'],['calendar'])
        await self.request('PATCH',f"/api/v1/recordings/{ready['id']}",{'title':'Renamed'})
        await self.kill_server();await self.start_server()
        _, after=await self.request('GET',f"/api/v1/recordings/{ready['id']}/uploads")
        self.assertEqual(len(after['items']),1)
        self.assertEqual(after['items'][0]['metadata'],metadata)
        _, disabled=await self.request('PUT','/api/v1/youtube/automation',{'enabled':False})
        self.assertFalse(disabled['enabled'])
        last=await self.capture(2)
        self.assertEqual((await self.request('GET',f"/api/v1/recordings/{last['id']}/uploads"))[1]['items'],[])
