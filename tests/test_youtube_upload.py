"""Public API and local resumable-upload fixture."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from recorder.server import create_app


class UploadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.initiations = []
        self.content = bytearray()
        self.fail_upload = False
        self.expire = False
        self.slow = False
        self.uploads = web.Application()
        async def initiate(request):
            self.initiations.append((dict(request.query), await request.json()))
            return web.Response(status=200, headers={'Location':str(self.provider.make_url('/session'))})
        async def session(request):
            total = 5
            if self.expire:return web.Response(status=404)
            if self.fail_upload:return web.Response(status=503)
            if self.slow:await asyncio.sleep(.3)
            content_range = request.headers['Content-Range']
            if content_range.startswith('bytes */'):
                if len(self.content) == total:
                    return web.json_response({'id':'video123'},status=201)
                return web.Response(status=308,headers={'Range':f'bytes=0-{len(self.content)-1}'}) if self.content else web.Response(status=308)
            start = int(content_range.split()[1].split('-')[0])
            self.assertEqual(start,len(self.content))
            self.content.extend(await request.read())
            return web.json_response({'id':'video123'},status=201) if len(self.content)==total else web.Response(status=308,headers={'Range':f'bytes=0-{len(self.content)-1}'})
        self.uploads.router.add_post('/init',initiate)
        self.uploads.router.add_put('/session',session)
        self.provider=TestServer(self.uploads);await self.provider.start_server()
        await self.start_app()

    async def start_app(self):
        self.app=create_app(self.tmp.name)
        conn=self.app['youtube_connection'];conn.data.update({'client_id':'client','client_secret':'secret','channel':{'id':'UCfixture','name':'Fixture'},'token':{'access_token':'token','refresh_token':'refresh','expires_at':9999999999},'reconnect_required':False});conn.save()
        self.app['youtube_provider']={'upload':str(self.provider.make_url('/init')),'session_hosts':{'127.0.0.1'}}
        self.client=TestClient(TestServer(self.app));await self.client.start_server()
        r={'id':'r1','state':'ready','started_at':'2026-10-14T00:00:00+00:00','filename':'ready.mp4','title':'Original','pinned':False,'progress':100,'interruptions':[]}
        if 'r1' not in self.app['youtube_connection'].service.records:
            folder=Path(self.tmp.name)/'media'/'r1';folder.mkdir();(folder/'ready.mp4').write_bytes(b'video')
            self.app['youtube_connection'].service.records['r1']=r;self.app['youtube_connection'].service.save(r)

    async def asyncTearDown(self):
        await self.client.close();await self.provider.close();self.tmp.cleanup()

    async def test_manual_upload_snapshots_metadata_and_preserves_local_media(self):
        body={'title':'Published title','description':'Test recording','privacy_status':'unlisted','made_for_kids':False,'notify_subscribers':False,'tags':['event'],'category_id':'22'}
        response=await self.client.post('/api/v1/recordings/r1/uploads',json=body,headers={'Idempotency-Key':'one'})
        self.assertEqual(response.status,201,await response.text())
        job=await response.json()
        again=await self.client.post('/api/v1/recordings/r1/uploads',json=body,headers={'Idempotency-Key':'one'})
        self.assertEqual((await again.json())['id'],job['id'])
        await self.client.patch('/api/v1/recordings/r1',json={'title':'Renamed'})
        for _ in range(80):
            found=await (await self.client.get(f"/api/v1/uploads/{job['id']}")).json()
            if found['state']=='completed':break
            await asyncio.sleep(.05)
        self.assertEqual(found['state'],'completed',found)
        self.assertEqual(found['url'],'https://www.youtube.com/watch?v=video123')
        self.assertEqual(self.initiations[0][1]['snippet']['title'],'Published title')
        self.assertEqual(self.initiations[0][1]['status'],{'privacyStatus':'unlisted','selfDeclaredMadeForKids':False})
        self.assertEqual(self.initiations[0][0]['notifySubscribers'],'false')
        self.assertEqual(self.content,b'video')
        self.assertTrue((Path(self.tmp.name)/'media'/'r1'/'ready.mp4').exists())

    async def test_protected_media_and_retry_from_saved_session(self):
        self.fail_upload=True
        response=await self.client.post('/api/v1/recordings/r1/uploads',json={'made_for_kids':False})
        self.assertEqual(response.status,201)
        job=await response.json()
        for _ in range(50):
            found=await (await self.client.get(f"/api/v1/uploads/{job['id']}")).json()
            if found['attempts']:break
            await asyncio.sleep(.02)
        self.assertEqual((await self.client.delete('/api/v1/recordings/r1')).status,409)
        self.assertTrue(self.app['youtube_connection'].service.uploads.protected('r1'))
        self.fail_upload=False
        # Retry starts from the saved session instead of another videos.insert.
        await self.client.post(f"/api/v1/uploads/{job['id']}/cancel",json={})
        self.assertEqual((await self.client.post(f"/api/v1/uploads/{job['id']}/requeue",json={})).status,201)
        for _ in range(80):
            jobs=(await (await self.client.get('/api/v1/uploads')).json())['items']
            if jobs[0]['state']=='completed':break
            await asyncio.sleep(.05)
        self.assertEqual(jobs[0]['state'],'completed')
        self.assertEqual((await self.client.delete('/api/v1/recordings/r1')).status,200)

    async def test_expired_saved_session_needs_reconciliation_before_requeue(self):
        self.expire=True
        response=await self.client.post('/api/v1/recordings/r1/uploads',json={'made_for_kids':True})
        job=await response.json()
        for _ in range(40):
            found=await (await self.client.get(f"/api/v1/uploads/{job['id']}")).json()
            if found['state']=='uncertain':break
            await asyncio.sleep(.02)
        self.assertEqual(found['state'],'uncertain')
        self.assertEqual(len(self.initiations),1)
        self.assertEqual((await self.client.post(f"/api/v1/uploads/{job['id']}/retry",json={})).status,409)
        self.assertEqual((await self.client.post(f"/api/v1/uploads/{job['id']}/requeue",json={})).status,409)
        self.assertEqual(len(self.initiations),1)
        self.assertEqual((await self.client.delete('/api/v1/recordings/r1')).status,409)
        self.assertEqual((await self.client.post(f"/api/v1/uploads/{job['id']}/release",json={})).status,200)
        self.assertEqual((await self.client.delete('/api/v1/recordings/r1')).status,200)

    async def test_single_worker_and_channel_binding(self):
        self.slow=True
        first=await (await self.client.post('/api/v1/recordings/r1/uploads',json={'made_for_kids':False})).json()
        second=await (await self.client.post('/api/v1/recordings/r1/uploads',json={'made_for_kids':False})).json()
        await asyncio.sleep(.05)
        states=[(await (await self.client.get(f"/api/v1/uploads/{j['id']}")).json())['state'] for j in (first,second)]
        self.assertLessEqual(states.count('uploading'),1)
        connection=self.app['youtube_connection'];connection.data['channel']={'id':'different','name':'Another'};connection.save()
        await asyncio.sleep(.35)
        self.assertNotEqual((await (await self.client.get(f"/api/v1/uploads/{second['id']}")).json())['state'],'completed')
        self.assertEqual((await self.client.post(f"/api/v1/uploads/{second['id']}/retry",json={})).status,409)
        changed=await self.client.post(f"/api/v1/uploads/{second['id']}/requeue",json={})
        self.assertEqual(changed.status,201)
        self.assertEqual((await changed.json())['channel_id'],'different')

    async def test_restart_probes_saved_session_and_preserves_known_link(self):
        self.fail_upload=True
        job=await (await self.client.post('/api/v1/recordings/r1/uploads',json={'made_for_kids':False})).json()
        for _ in range(60):
            found=await (await self.client.get(f"/api/v1/uploads/{job['id']}")).json()
            if found['attempts'] and self.initiations:break
            await asyncio.sleep(.02)
        self.assertEqual(len(self.initiations),1)
        await self.client.close()
        self.fail_upload=False
        await self.start_app()
        # A saved session is probed on restart; the worker never initiates again.
        await self.client.post(f"/api/v1/uploads/{job['id']}/retry",json={})
        for _ in range(80):
            found=await (await self.client.get(f"/api/v1/uploads/{job['id']}")).json()
            if found['state']=='completed':break
            await asyncio.sleep(.05)
        self.assertEqual(found['state'],'completed',found)
        self.assertEqual(len(self.initiations),1)
        self.assertEqual(found['url'],'https://www.youtube.com/watch?v=video123')

    async def test_capacity_and_multiple_job_protection(self):
        self.fail_upload=True
        first=await (await self.client.post('/api/v1/recordings/r1/uploads',json={'made_for_kids':False})).json()
        second=await (await self.client.post('/api/v1/recordings/r1/uploads',json={'made_for_kids':False})).json()
        await self.client.post(f"/api/v1/uploads/{first['id']}/cancel",json={})
        self.assertEqual((await self.client.delete('/api/v1/recordings/r1')).status,409)
        folder=Path(self.tmp.name)/'media'/'r1'
        (folder/'extra.bin').write_bytes(b'x'*(1024*1024))
        await self.client.put('/api/v1/storage',json={'storage_cap_bytes':1024*1024})
        self.assertTrue((folder/'ready.mp4').exists())
        await self.client.put('/api/v1/source',json={'source_url':'https://example.org/live.m3u8'})
        refused=await self.client.post('/api/v1/recordings',json={})
        self.assertEqual(refused.status,507,await refused.text())
        # Canceling the remaining job releases the final hold on local media.
        await self.client.post(f"/api/v1/uploads/{second['id']}/cancel",json={})
        for _ in range(30):
            if not self.app['youtube_connection'].service.uploads.active_job:break
            await asyncio.sleep(.02)
        self.assertEqual((await self.client.delete('/api/v1/recordings/r1')).status,200)
