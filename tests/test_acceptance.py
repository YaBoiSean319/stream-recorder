"""Acceptance tests against public HTTP and media-download seams."""
import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import unittest

import aiohttp
from aiohttp import web


def unused_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class RecorderAcceptance(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.media = self.root / 'hls'
        self.media.mkdir()
        self.offline = False
        self.fixture_log = (self.root / 'fixture.log').open('wb')
        self.fixture = subprocess.Popen([
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-re',
            '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=15',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-g', '15',
            '-c:a', 'aac', '-f', 'hls', '-hls_time', '1',
            '-hls_list_size', '6', '-hls_flags', 'delete_segments',
            str(self.media / 'stream.m3u8')], stdout=self.fixture_log, stderr=self.fixture_log)
        async def serve(request):
            if self.offline:
                raise web.HTTPServiceUnavailable()
            path = self.media / request.match_info['name']
            if not path.is_file():
                raise web.HTTPNotFound()
            return web.FileResponse(path, headers={'Access-Control-Allow-Origin': '*'})
        app = web.Application()
        app.router.add_get('/{name}', serve)
        self.fixture_runner = web.AppRunner(app)
        await self.fixture_runner.setup()
        self.fixture_port = unused_port()
        await web.TCPSite(self.fixture_runner, '127.0.0.1', self.fixture_port).start()
        for _ in range(80):
            if (self.media / 'stream.m3u8').exists():
                break
            await asyncio.sleep(.1)
        else:
            raise AssertionError('HLS fixture did not start')
        self.port = unused_port()
        self.base = f'http://127.0.0.1:{self.port}'
        self.source = f'http://127.0.0.1:{self.fixture_port}/stream.m3u8'
        self.client = aiohttp.ClientSession()
        self.process = None
        try:
            await self.start_server()
            await self.request('PUT', '/api/settings', {'source_url': self.source, 'storage_cap_bytes': 100_000_000})
        except BaseException:
            await self.asyncTearDown()
            raise

    async def start_server(self):
        self.server_log = (self.root / 'server.log').open('ab')
        self.process = subprocess.Popen([sys.executable, '-m', 'recorder'],
            env={**os.environ, 'PORT': str(self.port), 'DATA_DIR': str(self.root / 'data'), **getattr(self, 'server_env', {})},
            stdout=self.server_log, stderr=self.server_log)
        for _ in range(100):
            if self.process.poll() is not None:
                self.server_log.flush()
                raise AssertionError((self.root / 'server.log').read_text())
            try:
                status, _ = await self.request('GET', '/health', check=False)
                if status == 200:
                    return
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(.1)
        raise AssertionError('Recorder did not become healthy')

    async def kill_server(self):
        self.process.kill()
        await asyncio.to_thread(self.process.wait)
        self.server_log.close()
        self.process = None

    async def asyncTearDown(self):
        if self.process:
            self.process.terminate()
            try:
                await asyncio.wait_for(asyncio.to_thread(self.process.wait), 10)
            except asyncio.TimeoutError:
                self.process.kill()
                await asyncio.to_thread(self.process.wait)
            self.server_log.close()
        await self.client.close()
        await self.fixture_runner.cleanup()
        self.fixture.terminate()
        await asyncio.to_thread(self.fixture.wait)
        self.fixture_log.close()
        self.temp.cleanup()

    async def request(self, method, route, body=None, check=True):
        async with self.client.request(method, self.base + route, json=body) as response:
            raw = await response.read()
            try:
                result = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                result = raw
            if check:
                self.assertLess(response.status, 300, (response.status, result))
            return response.status, result

    async def wait_terminal(self, recording_id, timeout=35):
        for _ in range(int(timeout * 5)):
            _, recording = await self.request('GET', f'/api/recordings/{recording_id}')
            if recording['state'] in ('ready', 'error'):
                return recording
            await asyncio.sleep(.2)
        self.fail(f'Recording never became terminal: {recording}')

    async def capture(self, duration=4):
        _, recording = await self.request('POST', '/api/recordings', {'duration_seconds': duration})
        result = await self.wait_terminal(recording['id'])
        self.assertEqual(result['state'], 'ready', result)
        return result

    async def playable(self, recording):
        _, data = await self.request('GET', f"/api/recordings/{recording['id']}/download")
        output = self.root / (recording['id'] + '.mp4')
        output.write_bytes(data)
        probe = await asyncio.to_thread(subprocess.run, ['ffprobe', '-v', 'error', '-show_streams',
            '-show_format', '-of', 'json', str(output)], capture_output=True, check=True)
        metadata = json.loads(probe.stdout)
        self.assertEqual({s['codec_type'] for s in metadata['streams']}, {'video', 'audio'})
        self.assertGreater(float(metadata['format']['duration']), 1)
        decode = await asyncio.to_thread(subprocess.run, ['ffmpeg', '-v', 'error', '-i', str(output),
            '-f', 'null', '-'], capture_output=True)
        self.assertEqual(decode.returncode, 0, decode.stderr.decode())
        return metadata

    async def test_recording_produces_one_playable_download(self):
        recording = await self.capture()
        self.assertTrue(recording['filename'].endswith('.mp4'))
        await self.playable(recording)
        _, recordings = await self.request('GET', '/api/recordings')
        self.assertEqual([r['id'] for r in recordings], [recording['id']])

    async def test_only_one_racing_start_and_targeted_stop_is_retry_safe(self):
        results = await asyncio.gather(*(self.request('POST', '/api/recordings', {}, check=False) for _ in range(8)))
        winners = [r for status, r in results if status == 201]
        self.assertEqual(len(winners), 1)
        self.assertEqual([status for status, _ in results].count(409), 7)
        first = winners[0]
        status, _ = await self.request('DELETE', f"/api/recordings/{first['id']}", check=False)
        self.assertEqual(status, 409)
        await asyncio.sleep(3)
        await self.request('POST', f"/api/recordings/{first['id']}/stop", {})
        self.assertEqual((await self.wait_terminal(first['id']))['state'], 'ready')
        _, second = await self.request('POST', '/api/recordings', {})
        await self.request('POST', '/api/stop', {'recording_id': first['id']})
        _, status = await self.request('GET', '/api/status')
        self.assertEqual(status['active']['id'], second['id'])
        self.assertFalse(status['active']['stop_requested'])
        await self.request('POST', '/api/stop', {'recording_id': second['id']})
        await self.wait_terminal(second['id'])

    async def test_source_changes_apply_only_to_future_sessions(self):
        _, first = await self.request('POST', '/api/recordings', {'duration_seconds': 4})
        next_source = self.source + '?next=1'
        await self.request('PUT', '/api/settings', {'source_url': next_source})
        completed = await self.wait_terminal(first['id'])
        self.assertEqual(completed['source_url'], self.source)
        self.assertEqual(completed['deadline'], first['deadline'])
        second = await self.capture()
        self.assertEqual(second['source_url'], next_source)

    async def test_pinned_download_and_settings_survive_restart_then_manual_delete(self):
        first = await self.capture()
        await self.request('PATCH', f"/api/recordings/{first['id']}", {'pinned': True})
        await self.kill_server()
        await self.start_server()
        _, recovered = await self.request('GET', f"/api/recordings/{first['id']}")
        self.assertTrue(recovered['pinned'])
        self.assertEqual(recovered['state'], 'ready')
        _, settings = await self.request('GET', '/api/settings')
        self.assertEqual(settings['source_url'], self.source)
        await self.playable(recovered)
        _, usage = await self.request('GET', '/api/status')
        self.assertGreater(usage['storage']['media_bytes'], 0)
        self.assertGreater(usage['storage']['total_bytes'], usage['storage']['free_bytes'])
        await self.request('DELETE', f"/api/recordings/{first['id']}")
        _, recordings = await self.request('GET', '/api/recordings')
        self.assertEqual(recordings, [])
        status, _ = await self.request('GET', f"/api/recordings/{first['id']}/download", check=False)
        self.assertEqual(status, 404)

    async def test_manual_capture_recovers_after_force_kill(self):
        _, recording = await self.request('POST', '/api/recordings', {})
        await asyncio.sleep(4)
        await self.kill_server()
        await self.start_server()
        _, status = await self.request('GET', '/api/status')
        self.assertEqual(status['active']['id'], recording['id'])
        self.assertEqual(status['active']['started_at'], recording['started_at'])
        await asyncio.sleep(4)
        await self.request('POST', f"/api/recordings/{recording['id']}/stop", {})
        recovered = await self.wait_terminal(recording['id'])
        self.assertEqual(recovered['state'], 'ready', recovered)
        self.assertTrue(recovered['interruptions'])
        metadata = await self.playable(recovered)
        self.assertGreater(float(metadata['format']['duration']), 4)

    async def test_expired_deadline_finalizes_without_resuming_capture(self):
        _, recording = await self.request('POST', '/api/recordings', {'duration_seconds': 6})
        await asyncio.sleep(3)
        await self.kill_server()
        await asyncio.sleep(4)
        await self.start_server()
        recovered = await self.wait_terminal(recording['id'])
        self.assertEqual(recovered['deadline'], recording['deadline'])
        self.assertEqual(recovered['filename'], recording['filename'])
        self.assertEqual(recovered['state'], 'ready', recovered)
        _, status = await self.request('GET', '/api/status')
        self.assertIsNone(status['active'])
        await self.playable(recovered)

    async def test_source_outage_reconnects_preserving_footage(self):
        _, recording = await self.request('POST', '/api/recordings', {})
        await asyncio.sleep(4)
        self.offline = True
        for _ in range(120):
            _, status = await self.request('GET', f"/api/recordings/{recording['id']}")
            if status['interruptions']:
                break
            await asyncio.sleep(.2)
        self.assertTrue(status['interruptions'], status)
        self.offline = False
        await asyncio.sleep(6)
        await self.request('POST', f"/api/recordings/{recording['id']}/stop", {})
        completed = await self.wait_terminal(recording['id'])
        self.assertEqual(completed['state'], 'ready', completed)
        await self.playable(completed)

    async def test_validation_and_unavailable_download_do_not_release_session(self):
        for duration in (0, -1, True, 'soon'):
            status, _ = await self.request('POST', '/api/recordings', {'duration_seconds': duration}, check=False)
            self.assertEqual(status, 400)
        status, _ = await self.request('PUT', '/api/settings', {'source_url': 'file:///tmp/source'}, check=False)
        self.assertEqual(status, 400)
        _, recording = await self.request('POST', '/api/recordings', {'duration_seconds': 4})
        status, _ = await self.request('GET', f"/api/recordings/{recording['id']}/download", check=False)
        self.assertEqual(status, 409)
        status, _ = await self.request('POST', '/api/recordings', {}, check=False)
        self.assertEqual(status, 409)
        self.assertEqual((await self.wait_terminal(recording['id']))['state'], 'ready')

    async def test_retention_evicts_oldest_unpinned_recording(self):
        first = await self.capture()
        await self.request('PATCH', f"/api/recordings/{first['id']}", {'pinned': True})
        second = await self.capture()
        third = await self.capture()
        _, second_bytes = await self.request('GET', f"/api/recordings/{second['id']}/download")
        _, status = await self.request('GET', '/api/status')
        # Leave room for two completed files plus the documented 16 MiB admission reserve.
        cap = 16 * 1024**2 + status['storage']['media_bytes'] - len(second_bytes) + 8192
        await self.request('PUT', '/api/settings', {'storage_cap_bytes': cap})
        _, fourth = await self.request('POST', '/api/recordings', {})
        _, recordings = await self.request('GET', '/api/recordings')
        ids = {r['id'] for r in recordings}
        self.assertIn(first['id'], ids)
        self.assertNotIn(second['id'], ids)
        self.assertIn(third['id'], ids)
        await self.request('POST', f"/api/recordings/{fourth['id']}/stop", {})
        await self.wait_terminal(fourth['id'])

    async def test_cap_with_only_pinned_media_rejects_start_without_eviction(self):
        first = await self.capture()
        await self.request('PATCH', f"/api/recordings/{first['id']}", {'pinned': True})
        await self.request('PUT', '/api/settings', {'storage_cap_bytes': 1024**2})
        status, _ = await self.request('POST', '/api/recordings', {}, check=False)
        self.assertEqual(status, 507)
        _, recordings = await self.request('GET', '/api/recordings')
        self.assertEqual([r['id'] for r in recordings], [first['id']])
        await self.playable(first)

    async def test_incompatible_source_is_transcoded_to_playable_mp4(self):
        generated = await asyncio.to_thread(subprocess.run, ['ffmpeg', '-y', '-v', 'error',
            '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=15',
            '-f', 'lavfi', '-i', 'sine=sample_rate=48000', '-t', '8',
            '-c:v', 'mpeg2video', '-g', '15', '-c:a', 'mp2', '-f', 'hls', '-hls_time', '1',
            '-hls_list_size', '0', str(self.media / 'incompatible.m3u8')], capture_output=True)
        self.assertEqual(generated.returncode, 0, generated.stderr.decode())
        await self.request('PUT', '/api/settings', {'source_url': f'http://127.0.0.1:{self.fixture_port}/incompatible.m3u8'})
        recording = await self.capture(3)
        metadata = await self.playable(recording)
        self.assertEqual({s['codec_name'] for s in metadata['streams']}, {'h264', 'aac'})


    async def test_browser_controls_and_browser_closure(self):
        node_modules = os.environ.get('NODE_PATH') or os.environ.get('CODEX_PRIMARY_RUNTIME_NODE_MODULES') or ''
        if not os.environ.get('RUN_BROWSER_TESTS'):
            self.skipTest('Set RUN_BROWSER_TESTS=1 after installing Playwright Chromium')
        result = await asyncio.to_thread(subprocess.run, ['node', 'tests/browser-smoke.cjs'],
            env={**os.environ, 'NODE_PATH': node_modules, 'RECORDER_URL': self.base},
            capture_output=True, timeout=65)
        self.assertEqual(result.returncode, 0, result.stdout.decode() + result.stderr.decode())

    async def test_processing_recovers_without_duplicate_recording(self):
        _, recording = await self.request('POST', '/api/recordings', {})
        await asyncio.sleep(5)
        await self.request('POST', f"/api/recordings/{recording['id']}/stop", {})
        for _ in range(500):
            _, current = await self.request('GET', f"/api/recordings/{recording['id']}")
            if current['state'] == 'processing':
                break
            await asyncio.sleep(.01)
        self.assertEqual(current['state'], 'processing')
        status, _ = await self.request('POST', '/api/recordings', {}, check=False)
        self.assertEqual(status, 409)
        await self.kill_server()
        await self.start_server()
        completed = await self.wait_terminal(recording['id'])
        self.assertEqual(completed['state'], 'ready', completed)
        self.assertEqual(completed['filename'], recording['filename'])
        _, records = await self.request('GET', '/api/recordings')
        self.assertEqual(len(records), 1)
        await self.playable(completed)


    async def test_conversion_failure_preserves_footage_and_blocks_download(self):
        await self.kill_server()
        commands = self.root / 'commands'
        commands.mkdir()
        wrapper = commands / 'ffmpeg'
        wrapper.write_text('#!/bin/sh\ncase "$*" in *finalizing.mp4*) echo "Injected conversion failure" >&2; exit 1;; esac\nexec /usr/bin/ffmpeg "$@"\n')
        wrapper.chmod(0o755)
        self.server_env = {'PATH': str(commands) + ':' + os.environ['PATH']}
        await self.start_server()
        _, recording = await self.request('POST', '/api/recordings', {'duration_seconds': 4})
        completed = await self.wait_terminal(recording['id'])
        self.assertEqual(completed['state'], 'error')
        self.assertIn('conversion failed', completed['error'].lower())
        _, status = await self.request('GET', '/api/status')
        self.assertGreater(status['storage']['media_bytes'], 10000)
        self.assertIsNone(status['active'])
        code, _ = await self.request('GET', f"/api/recordings/{recording['id']}/download", check=False)
        self.assertEqual(code, 409)

    async def test_timed_capture_recovers_before_original_deadline(self):
        _, recording = await self.request('POST', '/api/recordings', {'duration_seconds': 9})
        await asyncio.sleep(3)
        await self.kill_server()
        await self.start_server()
        _, current = await self.request('GET', f"/api/recordings/{recording['id']}")
        self.assertEqual(current['deadline'], recording['deadline'])
        self.assertIn(current['state'], ('recovering', 'recording'))
        completed = await self.wait_terminal(recording['id'])
        self.assertEqual(completed['state'], 'ready', completed)
        await self.playable(completed)

    async def test_lowering_cap_during_capture_stops_safely_with_storage_error(self):
        _, recording = await self.request('POST', '/api/recordings', {})
        await asyncio.sleep(4)
        await self.request('PUT', '/api/settings', {'storage_cap_bytes': 1024**2})
        completed = await self.wait_terminal(recording['id'])
        self.assertEqual(completed['state'], 'error')
        self.assertIn('space', completed['error'].lower())
        _, status = await self.request('GET', '/api/status')
        self.assertIsNone(status['active'])
        self.assertGreater(status['storage']['media_bytes'], 10000)

    async def test_lowering_idle_cap_reclaims_oldest_unpinned_media(self):
        first = await self.capture(8)
        await self.request('PATCH', f"/api/recordings/{first['id']}", {'pinned': True})
        second = await self.capture(8)
        third = await self.capture(8)
        _, before = await self.request('GET', '/api/status')
        _, first_bytes = await self.request('GET', f"/api/recordings/{first['id']}/download")
        _, third_bytes = await self.request('GET', f"/api/recordings/{third['id']}/download")
        cap = max(1024**2, len(first_bytes) + len(third_bytes) + 16384)
        self.assertGreater(before['storage']['media_bytes'], cap)
        await self.request('PUT', '/api/settings', {'storage_cap_bytes': cap})
        _, records = await self.request('GET', '/api/recordings')
        ids = {r['id'] for r in records}
        self.assertIn(first['id'], ids)
        self.assertNotIn(second['id'], ids)
        self.assertIn(third['id'], ids)

    async def test_underlying_disk_shortage_stops_capture_preserving_pins(self):
        import shutil
        compiler = shutil.which('cc')
        if not compiler:
            self.skipTest('C compiler required for controlled OS free-space fixture')
        first = await self.capture()
        await self.request('PATCH', f"/api/recordings/{first['id']}", {'pinned': True})
        await self.kill_server()
        source = self.root / 'lowspace.c'
        library = self.root / 'lowspace.so'
        flag = self.root / 'disk-full'
        source.write_text(r'''
#define _GNU_SOURCE
#include <sys/statvfs.h>
#include <dlfcn.h>
#include <stdlib.h>
#include <unistd.h>
static int lowspace(void) { const char *p = getenv("LOWSPACE_FLAG"); return p && access(p, F_OK) == 0; }
int statvfs(const char *p, struct statvfs *s) {
    static int (*real)(const char *, struct statvfs *) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "statvfs");
    int result = real(p, s); if (!result && lowspace()) s->f_bavail = s->f_bfree = 0;
    return result;
}
int statvfs64(const char *p, struct statvfs64 *s) {
    static int (*real)(const char *, struct statvfs64 *) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "statvfs64");
    int result = real(p, s); if (!result && lowspace()) s->f_bavail = s->f_bfree = 0;
    return result;
}
''')
        compile_result = await asyncio.to_thread(subprocess.run, [compiler, '-shared', '-fPIC', str(source), '-o', str(library), '-ldl'], capture_output=True)
        self.assertEqual(compile_result.returncode, 0, compile_result.stderr.decode())
        self.server_env = {'LD_PRELOAD': str(library), 'LOWSPACE_FLAG': str(flag)}
        await self.start_server()
        flag.touch()
        _, status = await self.request('GET', '/api/status')
        self.assertEqual(status['storage']['free_bytes'], 0)
        code, _ = await self.request('POST', '/api/recordings', {}, check=False)
        self.assertEqual(code, 507)
        flag.unlink()
        _, recording = await self.request('POST', '/api/recordings', {})
        await asyncio.sleep(4)
        flag.touch()
        completed = await self.wait_terminal(recording['id'])
        self.assertEqual(completed['state'], 'error')
        self.assertIn('space', completed['error'].lower())
        flag.unlink()
        _, records = await self.request('GET', '/api/recordings')
        self.assertIn(first['id'], [r['id'] for r in records])
        await self.playable(first)

    async def test_successful_encoder_with_corrupt_payload_is_not_published(self):
        await self.kill_server()
        commands = self.root / 'corrupt-commands'
        commands.mkdir()
        script = commands / 'corrupt.py'
        script.write_text('''import subprocess, sys
from pathlib import Path
result = subprocess.run(['/usr/bin/ffmpeg', *sys.argv[1:]])
if result.returncode == 0 and sys.argv[-1].endswith('finalizing.mp4'):
    path = Path(sys.argv[-1]); data = bytearray(path.read_bytes())
    marker = data.index(b'mdat'); size = int.from_bytes(data[marker-4:marker], 'big')
    data[marker+4:marker-4+size] = bytes(size-8)
    path.write_bytes(data)
sys.exit(result.returncode)
''')
        wrapper = commands / 'ffmpeg'
        wrapper.write_text(f'#!/bin/sh\nexec {sys.executable} {script} "$@"\n')
        wrapper.chmod(0o755)
        self.server_env = {'PATH': str(commands) + ':' + os.environ['PATH']}
        await self.start_server()
        _, recording = await self.request('POST', '/api/recordings', {'duration_seconds': 4})
        completed = await self.wait_terminal(recording['id'])
        self.assertEqual(completed['state'], 'error', 'Successful encoder exit with corrupt footage must not become Ready')
        self.assertIn('validation', completed['error'].lower())
        code, _ = await self.request('GET', f"/api/recordings/{recording['id']}/download", check=False)
        self.assertEqual(code, 409)

    async def test_cap_exhaustion_during_processing_preserves_source(self):
        await self.kill_server()
        commands = self.root / 'slow-converter'
        commands.mkdir()
        script = commands / 'slow.py'
        script.write_text('import os, sys, time\nif sys.argv[-1].endswith("finalizing.mp4"): time.sleep(10)\nos.execv("/usr/bin/ffmpeg", ["ffmpeg", *sys.argv[1:]])\n')
        wrapper = commands / 'ffmpeg'
        wrapper.write_text(f'#!/bin/sh\nexec {sys.executable} {script} "$@"\n')
        wrapper.chmod(0o755)
        self.server_env = {'PATH': str(commands) + ':' + os.environ['PATH']}
        await self.start_server()
        _, recording = await self.request('POST', '/api/recordings', {'duration_seconds': 4})
        for _ in range(150):
            _, current = await self.request('GET', f"/api/recordings/{recording['id']}")
            if current['state'] == 'processing':
                break
            await asyncio.sleep(.1)
        self.assertEqual(current['state'], 'processing')
        await self.request('PUT', '/api/settings', {'storage_cap_bytes': 1024**2})
        completed = await self.wait_terminal(recording['id'])
        self.assertEqual(completed['state'], 'error', completed)
        self.assertIn('space', completed['error'].lower())
        _, status = await self.request('GET', '/api/status')
        self.assertGreater(status['storage']['media_bytes'], 10000)
