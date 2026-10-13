import asyncio,tempfile,unittest
from aiohttp.test_utils import TestClient,TestServer
from recorder.server import create_app

class ApiTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.client=TestClient(TestServer(create_app(self.tmp.name)));await self.client.start_server()
 async def asyncTearDown(self):
  await self.client.close();self.tmp.cleanup()
 async def test_admission_validation_and_retry_safe_stop(self):
  r=await self.client.post('/api/recordings',json={'duration_seconds':-1});self.assertEqual(r.status,400)
  await self.client.put('/api/settings',json={'source_url':'http://127.0.0.1:1/live.m3u8'})
  results=await asyncio.gather(*(self.client.post('/api/recordings',json={}) for _ in range(2)))
  self.assertEqual(sorted(r.status for r in results),[201,409])
  admitted=await next(r for r in results if r.status==201).json()
  self.assertEqual((await self.client.delete('/api/recordings/'+admitted['id'])).status,409)
  self.assertEqual((await self.client.post('/api/recordings/'+admitted['id']+'/stop')).status,200)
  self.assertEqual((await self.client.post('/api/recordings/'+admitted['id']+'/stop')).status,200)
