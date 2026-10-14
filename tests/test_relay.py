import tempfile
import unittest
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient

from recorder.source import SourceRelay

class RelayAcceptance(unittest.IsolatedAsyncioTestCase):
    async def test_ranges_redirects_and_failed_sources_are_bounded(self):
        async def upstream(request):
            if request.path == '/root':
                raise web.HTTPFound('/relative/media')
            if request.path == '/relative/media':
                return web.Response(text='#EXTM3U\n#EXT-X-MAP:URI="../bytes"\n#EXT-X-BYTERANGE:3@2\n../bytes\n')
            if request.path == '/bytes':
                self.assertEqual(request.headers.get('Range'), 'bytes=2-4')
                return web.Response(body=b'cde', status=206, headers={'Content-Range': 'bytes 2-4/6', 'Accept-Ranges': 'bytes'})
            if request.path == '/bad':
                return web.Response(text='#EXTM3U\nfile:///private.ts\n')
            if request.path == '/loop':
                raise web.HTTPFound('/loop')
            raise web.HTTPUnauthorized()
        source = web.Application(); source.router.add_get('/{tail:.*}', upstream)
        async with TestServer(source) as server:
            relay = SourceRelay()
            app = web.Application(); app.router.add_get('/preview/{token}', relay.handle)
            async with TestClient(TestServer(app)) as client:
                root = relay.register({'source_url': str(server.make_url('/root'))})
                text = await (await client.get(root)).text()
                self.assertIn('#EXT-X-BYTERANGE:3@2', text)
                response = await client.get(text.splitlines()[-1], headers={'Range': 'bytes=2-4'})
                self.assertEqual(response.status, 206)
                self.assertEqual(response.headers['Content-Range'], 'bytes 2-4/6')
                self.assertEqual(await response.read(), b'cde')
                for path in ('/bad', '/loop', '/missing'):
                    url = relay.register({'source_url': str(server.make_url(path))})
                    response = await client.get(url)
                    self.assertEqual(response.status, 502)
                    self.assertEqual(await response.text(), 'Configured source resource unavailable')
            await relay.close()

    async def test_capture_references_are_not_public_preview_resources(self):
        source = web.Application(); source.router.add_get('/root', lambda request: web.Response(text='#EXTM3U\n'))
        async with TestServer(source) as server:
            relay = SourceRelay()
            app = web.Application(); app.router.add_get('/preview/{token}', relay.handle)
            async with TestClient(TestServer(app)) as client:
                capture = await relay.capture_url({'source_url': str(server.make_url('/root'))})
                token = capture.rsplit('/', 1)[-1]
                self.assertEqual((await client.get('/preview/' + token)).status, 404)
            await relay.close()
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
