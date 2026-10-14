import tempfile
import unittest
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient

from recorder.source import SourceRelay

class RelayAcceptance(unittest.IsolatedAsyncioTestCase):
    async def test_credentials_are_not_forwarded_to_cross_origin_resources(self):
        seen = []
        async def other(request):
            seen.append(dict(request.headers))
            return web.Response(body=b'media')
        cross = web.Application(); cross.router.add_get('/segment.ts', other)
        async with TestServer(cross) as cross_server:
            async def upstream(request):
                self.assertEqual(request.headers.get('Authorization'), 'Bearer private-token')
                self.assertEqual(request.headers.get('X-Private'), 'private-header')
                return web.Response(text='#EXTM3U\n#EXTINF:1,\n' + str(cross_server.make_url('/segment.ts')) + '\n')
            source = web.Application(); source.router.add_get('/stream', upstream)
            async with TestServer(source) as server:
                relay = SourceRelay()
                app = web.Application(); app.router.add_get('/preview/{token}', relay.handle)
                async with TestClient(TestServer(app)) as client:
                    root = relay.register({'source_url': str(server.make_url('/stream')), 'source_bearer_token': 'private-token', 'source_headers': {'X-Private': 'private-header'}})
                    response = await client.get(root)
                    self.assertEqual(response.status, 200)
                    segment = (await response.text()).splitlines()[-1]
                    self.assertEqual(await (await client.get(segment)).read(), b'media')
                    self.assertNotIn('Authorization', seen[0])
                    self.assertNotIn('X-Private', seen[0])
                await relay.close()
    async def test_nested_resources_are_opaque_and_restricted(self):
        async def upstream(request):
            if request.path == '/master.m3u8':
                return web.Response(text='#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nnested/media.m3u8?secret=value\n')
            if request.path == '/nested/media.m3u8':
                return web.Response(text='#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:1,\npart.ts\n')
            return web.Response(body=b'fixture')
        source = web.Application(); source.router.add_get('/{tail:.*}', upstream)
        async with TestServer(source) as server:
            relay = SourceRelay()
            app = web.Application(); app.router.add_get('/preview/{token}', relay.handle)
            async with TestClient(TestServer(app)) as client:
                root = relay.register({'source_url': str(server.make_url('/master.m3u8'))})
                response = await client.get(root)
                text = await response.text()
                self.assertNotIn('secret', text)
                nested = text.splitlines()[-1]
                response = await client.get(nested)
                text = await response.text()
                self.assertNotIn('key.bin', text)
                segment = text.splitlines()[-1]
                self.assertEqual(await (await client.get(segment)).read(), b'fixture')
                self.assertEqual((await client.get('/preview/http://example.com')).status, 404)
                relay.invalidate_preview()
                self.assertEqual((await client.get(root)).status, 404)
            await relay.close()
