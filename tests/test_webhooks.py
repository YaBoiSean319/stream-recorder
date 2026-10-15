"""Webhook API observed through a local HTTP receiver and durable restart."""
import asyncio
import hashlib
import hmac
import tempfile
import unittest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from recorder.server import create_app


class WebhookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.received = []
        self.fail = False
        receiver = web.Application()
        async def accept(request):
            body = await request.read()
            self.received.append((dict(request.headers), body))
            return web.Response(status=503 if self.fail else 204)
        receiver.router.add_post('/hook', accept)
        self.receiver = TestServer(receiver)
        await self.receiver.start_server()
        self.client = TestClient(TestServer(create_app(self.tmp.name)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await self.receiver.close()
        self.tmp.cleanup()

    async def test_signed_delivery_has_stable_id_and_secret_is_masked(self):
        url = str(self.receiver.make_url('/hook'))
        response = await self.client.post('/api/v1/webhooks', json={'url':url,'events':['recording.completed'],'secret':'super-secret-signing'})
        self.assertEqual(response.status, 201, await response.text())
        destination = await response.json()
        self.assertNotIn('super-secret-signing', str(destination))
        service = self.client.server.app['youtube_connection'].service
        record = {'id':'r1','state':'ready','started_at':'2026-10-14T00:00:00+00:00','filename':'safe.mp4','interruptions':[],'progress':100}
        service.records['r1'] = record
        service.save(record)
        for _ in range(100):
            if self.received: break
            await asyncio.sleep(.02)
        self.assertEqual(len(self.received), 1)
        headers, body = self.received[0]
        signature = hmac.new(b'super-secret-signing', headers['X-Stream-Recorder-Timestamp'].encode()+b'.'+body, hashlib.sha256).hexdigest()
        self.assertEqual(headers['X-Stream-Recorder-Signature'], 'sha256='+signature)
        deliveries = await (await self.client.get('/api/v1/webhooks/deliveries')).json()
        self.assertEqual(deliveries['items'][0]['event_id'], headers['X-Stream-Recorder-Event-Id'])
        self.assertNotIn('super-secret-signing', str(deliveries))

    async def test_retries_survive_restart_and_failed_attempt_is_visible(self):
        self.fail = True
        dest = await self.client.post('/api/v1/webhooks',json={'url':str(self.receiver.make_url('/hook')),'events':['upload.failed'],'secret':'x'*32})
        self.assertEqual(dest.status,201)
        service = self.client.server.app['youtube_connection'].service
        service.webhooks.emit('upload.failed', 'job1', {'upload_id':'job1','recording_id':'r1','state':'failed'})
        for _ in range(100):
            if self.received: break
            await asyncio.sleep(.02)
        self.assertTrue(self.received)
        before = await (await self.client.get('/api/v1/webhooks/deliveries')).json()
        self.assertEqual(before['items'][0]['attempts'],1)
        event_id = before['items'][0]['event_id']
        await self.client.close()
        self.client=TestClient(TestServer(create_app(self.tmp.name)))
        await self.client.start_server()
        self.fail = False
        for _ in range(200):
            result=await (await self.client.get('/api/v1/webhooks/deliveries')).json()
            if result['items'][0]['status']=='delivered':break
            await asyncio.sleep(.05)
        self.assertEqual(result['items'][0]['status'],'delivered',result)
        self.assertEqual(result['items'][0]['event_id'],event_id)

    async def test_url_and_redirect_controls(self):
        for url in ['http://127.0.0.2/hook','http://169.254.169.254/hook','https://user:pass@example.com/hook','https://example.com/hook#fragment']:
            response=await self.client.post('/api/v1/webhooks',json={'url':url,'events':['recording.completed'],'secret':'x'*32})
            self.assertEqual(response.status,400,url)

    async def test_redirect_is_not_followed_and_upload_transition_emits_only_result(self):
        redirected=[]
        target=web.Application()
        async def landed(request):
            redirected.append(await request.read())
            return web.Response(status=204)
        target.router.add_post('/landing',landed)
        server=TestServer(target);await server.start_server()
        self.addAsyncCleanup(server.close)
        # The configured receiver can redirect, but the signed request is never forwarded.
        first=[]
        async def redirect(request):
            first.append((dict(request.headers),await request.read()))
            return web.Response(status=307,headers={'Location':str(server.make_url('/landing'))})
        sender=web.Application();sender.router.add_post('/redirect',redirect)
        redirect_server=TestServer(sender);await redirect_server.start_server();self.addAsyncCleanup(redirect_server.close)
        response=await self.client.post('/api/v1/webhooks',json={'url':str(redirect_server.make_url('/redirect')),'events':['upload.completed'],'secret':'this-is-a-test-secret'})
        self.assertEqual(response.status,201)
        service=self.client.server.app['youtube_connection'].service
        job={'id':'u1','recording_id':'r1','state':'queued','created_at':'2026-10-14T00:00:00+00:00'}
        service.uploads.jobs['u1']=job
        service.uploads.save(job)
        job.update({'state':'completed','url':'https://www.youtube.com/watch?v=abc'})
        service.uploads.save(job)
        for _ in range(80):
            if first:break
            await asyncio.sleep(.02)
        self.assertEqual(len(first),1)
        self.assertFalse(redirected)
        self.assertEqual(__import__('json').loads(first[0][1])['data']['url'],job['url'])

    async def test_operator_key_cannot_read_configuration_or_delivery_history(self):
        from recorder.auth import digest
        access=self.client.server.app['recorder_access']
        access.state['enabled']=True
        access.state['keys']=[{'id':'operator','role':'operator','hash':digest('operator-token'),'permissions':['uploads:read'],'expires_at':None}]
        access.save()
        headers={'Authorization':'Bearer operator-token'}
        for path in ['/api/v1/webhooks','/api/v1/webhooks/deliveries']:
            response=await self.client.get(path,headers=headers)
            self.assertEqual(response.status,403,path)
        response=await self.client.post('/api/v1/webhooks',headers=headers,json={'url':str(self.receiver.make_url('/hook')),'events':['recording.completed'],'secret':'this-is-a-test-secret'})
        self.assertEqual(response.status,403)
        access.state['keys'].append({'id':'admin','role':'admin','hash':digest('admin-token'),'permissions':['webhooks:manage'],'expires_at':None})
        access.save()
        response=await self.client.post('/api/v1/webhooks',headers={'Authorization':'Bearer admin-token'},json={'url':str(self.receiver.make_url('/hook')),'events':['recording.completed'],'secret':'this-is-a-test-secret'})
        self.assertEqual(response.status,201,await response.text())
