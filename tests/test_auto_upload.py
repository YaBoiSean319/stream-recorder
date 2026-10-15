"""Automatic upload behavior through HTTP recording and upload endpoints."""
import asyncio
import unittest
from tests import test_youtube_upload as upload_fixture


class AutoUploadTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=upload_fixture.UploadTests.asyncSetUp
    asyncTearDown=upload_fixture.UploadTests.asyncTearDown
    start_app=upload_fixture.UploadTests.start_app
    async def test_defaults_are_off_and_require_audience_and_connection(self):
        response=await self.client.get('/api/v1/youtube/automation')
        self.assertEqual(response.status,200)
        self.assertFalse((await response.json())['enabled'])
        response=await self.client.put('/api/v1/youtube/automation',json={'enabled':True})
        self.assertEqual(response.status,400)
        response=await self.client.put('/api/v1/youtube/automation',json={'enabled':True,'made_for_kids':False,'title_template':'{unexpected}'})
        self.assertEqual(response.status,400)
        response=await self.client.put('/api/v1/youtube/automation',json={'enabled':True,'made_for_kids':False,'title_template':'{recording_title} - {source_name} {start_date} {start_time}','description_template':'Recorded {start_date}','source_name':'Camera','tags':['event']})
        self.assertEqual(response.status,200,await response.text())
        self.assertTrue((await response.json())['enabled'])

    async def test_no_backfill_and_only_explicit_future_clean_completion_queues(self):
        await self.client.put('/api/v1/youtube/automation',json={'enabled':True,'made_for_kids':False,'title_template':'{recording_title} / {source_name} / {start_date} {start_time}'})
        self.assertEqual((await (await self.client.get('/api/v1/recordings/r1/uploads')).json())['items'],[])
        service=self.app['youtube_connection'].service
        record={'id':'new','state':'recording','started_at':'2026-10-14T12:30:00+00:00','filename':'new.mp4','title':'Lecture','pinned':False,'progress':0,'interruptions':[]}
        (service.media/'new').mkdir();(service.media/'new'/'new.mp4').write_bytes(b'video')
        service.records['new']=record
        service.auto_upload.prepare(record, {'description':'From calendar','tags':['custom']})
        service.save(record)
        record['state']='ready';service.save(record)
        service.uploads.enqueue_automatic(record)
        service.uploads.enqueue_automatic(record)
        jobs=(await (await self.client.get('/api/v1/recordings/new/uploads')).json())['items']
        self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0]['metadata']['title'],'Lecture / Stream / 2026-10-14 12:30 UTC')
        self.assertEqual(jobs[0]['metadata']['description'],'From calendar')
        self.assertEqual(jobs[0]['metadata']['tags'],['custom'])
        await self.client.patch('/api/v1/recordings/new',json={'title':'Renamed'})
        self.assertEqual((await (await self.client.get('/api/v1/recordings/new/uploads')).json())['items'][0]['metadata']['title'],jobs[0]['metadata']['title'])

    async def test_interrupted_ready_footage_requires_manual_upload(self):
        await self.client.put('/api/v1/youtube/automation',json={'enabled':True,'made_for_kids':False})
        service=self.app['youtube_connection'].service
        record={'id':'recovered','state':'ready','started_at':'2026-10-14T12:30:00+00:00',
                'filename':'recovered.mp4','title':'Review','pinned':False,'progress':100,
                'interruptions':[{'at':'2026-10-14T12:31:00+00:00','reason':'Service restart'}]}
        (service.media/'recovered').mkdir();(service.media/'recovered'/'recovered.mp4').write_bytes(b'video')
        service.auto_upload.prepare(record)
        service.records['recovered']=record;service.save(record)
        service.uploads.enqueue_automatic(record)
        self.assertEqual((await (await self.client.get('/api/v1/recordings/recovered/uploads')).json())['items'],[])
        result=await self.client.post('/api/v1/recordings/recovered/uploads',json={'made_for_kids':False})
        self.assertEqual(result.status,201,await result.text())

    async def test_automatic_job_uses_provider_and_returns_video_link(self):
        await self.client.put('/api/v1/youtube/automation',json={'enabled':True,'made_for_kids':False,
            'title_template':'{recording_title}', 'privacy_status':'unlisted','notify_subscribers':False})
        service=self.app['youtube_connection'].service
        record={'id':'future','state':'recording','started_at':'2026-10-14T12:30:00+00:00',
                'filename':'future.mp4','title':'Weekly update','pinned':False,'progress':0,'interruptions':[]}
        folder=service.media/'future';folder.mkdir();(folder/'future.mp4').write_bytes(b'video')
        service.auto_upload.prepare(record)
        service.records['future']=record;service.save(record)
        record['state']='ready';service.save(record);service.uploads.enqueue_automatic(record)
        for _ in range(80):
            jobs=(await (await self.client.get('/api/v1/recordings/future/uploads')).json())['items']
            if jobs[0]['state']=='completed':break
            await asyncio.sleep(.05)
        self.assertEqual(jobs[0]['state'],'completed',jobs[0])
        self.assertEqual(jobs[0]['url'],'https://www.youtube.com/watch?v=video123')
        self.assertEqual(self.initiations[0][1]['snippet']['title'],'Weekly update')
        self.assertTrue((folder/'future.mp4').exists())
