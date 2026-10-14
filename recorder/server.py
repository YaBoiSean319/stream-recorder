import asyncio, ctypes, datetime as dt, fcntl, json, os, shutil, signal, sqlite3, time, uuid
from pathlib import Path
from urllib.parse import urlsplit
from aiohttp import web
from .source import SourceRelay, source_config, public_record, public_source, update_source
ACTIVE = {'recording', 'reconnecting', 'recovering', 'processing'}
SUPERVISOR_PID = os.getpid()

def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def epoch(value):
    return dt.datetime.fromisoformat(value).timestamp()

def parent_death_signal():
    ctypes.CDLL(None).prctl(1, signal.SIGKILL)
    if os.getppid() != SUPERVISOR_PID:
        os.kill(os.getpid(), signal.SIGKILL)

class Recorder:

    def __init__(self, path):
        self.root = Path(path)
        self.root.mkdir(parents=True, exist_ok=True)
        self.media = self.root / 'media'
        self.media.mkdir(exist_ok=True)
        self.lockfile = open(self.root / 'service.lock', 'a')
        fcntl.flock(self.lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.db = sqlite3.connect(self.root / 'state.sqlite')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,payload TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY,payload TEXT)')
        row = self.db.execute('SELECT payload FROM settings WHERE id=1').fetchone()
        defaults = {'source_url': '', 'storage_cap_bytes': 100 * 1024 ** 3, 'auth_enabled': False}
        self.settings = {**defaults, **(json.loads(row[0]) if row else {})}
        self.db.execute('INSERT OR REPLACE INTO settings VALUES(1,?)', (json.dumps(self.settings),))
        self.db.commit()
        self.records = {i: json.loads(p) for i, p in self.db.execute('SELECT id,payload FROM records')}
        self.gate = asyncio.Lock()
        self.task = None
        self.proc = None
        self.closing = False
        self.conversion_log = None
        self.relay = SourceRelay()

    def save(self, r):
        self.db.execute('INSERT OR REPLACE INTO records VALUES(?,?)', (r['id'], json.dumps(r)))
        self.db.commit()

    def active(self):
        return next((r for r in self.records.values() if r['state'] in ACTIVE), None)

    def usage(self):
        disk = shutil.disk_usage(self.root)
        media_bytes = sum(p.stat().st_size for p in self.media.rglob('*') if p.is_file())
        cap = self.settings['storage_cap_bytes']
        return {'media_bytes': media_bytes, 'cap_bytes': cap, 'total_bytes': disk.total, 'free_bytes': disk.free, 'cap_exceeded': media_bytes > cap}

    def remove(self, r):
        shutil.rmtree(self.media / r['id'], ignore_errors=True)
        self.records.pop(r['id'])
        self.db.execute('DELETE FROM records WHERE id=?', (r['id'],))
        self.db.commit()

    def space(self, need):
        for r in sorted(self.records.values(), key=lambda r: r['started_at']):
            u = self.usage()
            if u['media_bytes'] + need <= u['cap_bytes'] and u['free_bytes'] >= need:
                return True
            if r['state'] == 'ready' and (not r['pinned']):
                self.remove(r)
        u = self.usage()
        return u['media_bytes'] + need <= u['cap_bytes'] and u['free_bytes'] >= need

    async def startup(self, app):
        r = self.active()
        if r:
            r['interruptions'].append({'at': stamp(), 'reason': 'Service restart; footage during downtime unavailable'})
            if r['state'] != 'processing':
                r['state'] = 'recovering'
            self.save(r)
            self.task = asyncio.create_task(self.run(r))

    async def shutdown(self, app):
        self.closing = True
        if self.proc and self.proc.returncode is None:
            self.proc.send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(self.proc.wait(), 10)
            except asyncio.TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if self.conversion_log and not self.conversion_log.closed:
            self.conversion_log.close()
        await self.relay.close()
        self.db.close()
        self.lockfile.close()

    async def terminate(self):
        p = self.proc
        if p and p.returncode is None:
            try:
                p.send_signal(signal.SIGINT)
            except ProcessLookupError:
                await p.wait()
                return
            try:
                await asyncio.wait_for(p.wait(), 5)
            except asyncio.TimeoutError:
                p.kill()
                await p.wait()

    async def run(self, r):
        folder = self.media / r['id']
        folder.mkdir(exist_ok=True)
        log = None
        try:
            while r['state'] != 'processing' and (not r.get('stop_requested')) and (not r['deadline'] or epoch(r['deadline']) > time.time()):
                if not self.space(16 * 1024 ** 2):
                    r['error'] = 'Storage exhausted: free space or unpin/delete recordings'
                    r['stop_requested'] = True
                    self.save(r)
                    break
                index = max([int(p.stem.split('-')[-1]) for p in folder.glob('capture-*.ts')] + [-1]) + 1
                log = open(folder / 'capture.log', 'ab')
                capture_url = await self.relay.capture_url(r.get('source_config', {'source_url': r.get('source_url', '')}))
                self.proc = await asyncio.create_subprocess_exec('ffmpeg', '-hide_banner', '-loglevel', 'warning', '-nostdin', '-rw_timeout', '10000000', '-allowed_extensions', 'ALL', '-i', capture_url, '-map', '0:v?', '-map', '0:a?', '-c', 'copy', '-f', 'segment', '-segment_time', '2', '-segment_format', 'mpegts', '-reset_timestamps', '1', '-segment_start_number', str(index), str(folder / 'capture-%06d.ts'), stdout=asyncio.subprocess.DEVNULL, stderr=log, start_new_session=True, preexec_fn=parent_death_signal)
                r['state'] = 'recording'
                self.save(r)
                last_bytes = -1
                last_growth = time.monotonic()
                while self.proc.returncode is None:
                    await asyncio.sleep(0.5)
                    if self.closing:
                        log.close()
                        return
                    if r.get('stop_requested') or (r['deadline'] and epoch(r['deadline']) <= time.time()):
                        await self.terminate()
                        break
                    current_bytes = sum(p.stat().st_size for p in folder.glob('capture-*.ts'))
                    if current_bytes != last_bytes:
                        last_bytes = current_bytes
                        last_growth = time.monotonic()
                    if time.monotonic() - last_growth > 30:
                        await self.terminate()
                        break
                    if not self.space(16 * 1024 ** 2):
                        r['error'] = 'Storage exhausted; captured footage preserved'
                        r['stop_requested'] = True
                        self.save(r)
                        await self.terminate()
                        break
                await self.proc.wait()
                log.close()
                if not r.get('stop_requested') and (not r['deadline'] or epoch(r['deadline']) > time.time()):
                    r['state'] = 'reconnecting'
                    r['interruptions'].append({'at': stamp(), 'reason': 'Source disconnected; retrying in 2 seconds'})
                    self.save(r)
                    await asyncio.sleep(2)
            if self.closing:
                return
            r['state'] = 'processing'
            r['stop_requested'] = True
            self.save(r)
            await self.finalize(r, folder)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.terminate()
            r['state'] = 'error'
            r['error'] = str(exc)
            self.save(r)
        finally:
            if log and (not log.closed):
                log.close()
            self.proc = None

    async def probe(self, path):
        p = await asyncio.create_subprocess_exec('ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, preexec_fn=parent_death_signal)
        try:
            out, _ = await asyncio.wait_for(p.communicate(), 30)
            if p.returncode:
                raise RuntimeError('Media inspection failed; source footage retained')
            return json.loads(out)
        finally:
            if p.returncode is None:
                p.kill()
                await p.wait()

    async def validate_decode(self, path, folder):
        # A valid container header does not establish that its media is playable.
        # Decode every selected track before publishing the download.
        with open(folder / 'validation.log', 'wb') as log:
            self.proc = await asyncio.create_subprocess_exec(
                'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
                '-xerror', '-err_detect', 'explode', '-i', str(path),
                '-map', '0:v?', '-map', '0:a?', '-f', 'null', '-',
                stdout=asyncio.subprocess.DEVNULL, stderr=log,
                start_new_session=True, preexec_fn=parent_death_signal)
            try:
                await self.proc.wait()
                return self.proc.returncode == 0
            finally:
                if self.proc.returncode is None:
                    self.proc.kill()
                    await self.proc.wait()

    async def finalize(self, r, folder):
        pieces = []
        duration = 0
        expected_tracks = set()
        for p in sorted(folder.glob('capture-*.ts')):
            try:
                info = await self.probe(p)
                expected_tracks.update(stream['codec_type'] for stream in info['streams'] if stream['codec_type'] in {'video', 'audio'})
                duration += float(info['format'].get('duration', 0))
                pieces.append(p)
            except Exception:
                r['interruptions'].append({'at': stamp(), 'reason': 'Unusable capture segment retained for salvage: ' + p.name})
                self.save(r)
        if not pieces:
            raise RuntimeError('No usable footage captured. Check source URL and capture.log; retry recording.')
        size = sum((p.stat().st_size for p in pieces))
        if not self.space(max(size * 3, 16 * 1024 ** 2)):
            raise RuntimeError('Insufficient conversion space. Source footage retained in media/<recording-id>; free space and salvage with FFmpeg.')
        manifest = folder / 'concat.txt'
        manifest.write_text(''.join(("file '" + p.name + "'\n" for p in pieces)))
        out = folder / 'finalizing.mp4'
        info = await self.probe(pieces[0])
        compatible = all((s['codec_name'] in {'h264', 'hevc', 'aac', 'mp3', 'alac'} for s in info['streams'] if s['codec_type'] in {'audio', 'video'}))
        for copy in [True, False] if compatible else [False]:
            args = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-nostdin', '-f', 'concat', '-safe', '0', '-i', str(manifest), '-map', '0:v?', '-map', '0:a?']
            args += ['-c', 'copy'] if copy else ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-c:a', 'aac', '-b:a', '192k']
            args += ['-movflags', '+faststart', '-progress', 'pipe:1', str(out)]
            log = open(folder / 'conversion.log', 'wb')
            self.conversion_log = log
            self.proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=log, start_new_session=True, preexec_fn=parent_death_signal)
            async for line in self.proc.stdout:
                if line.startswith(b'out_time_us='):
                    try:
                        r['progress'] = min(99, round(int(line.split(b'=')[1]) / max(duration * 1000000, 1) * 100))
                        self.save(r)
                    except ValueError:
                        pass
                if not self.space(4 * 1024 ** 2):
                    await self.terminate()
                    log.close()
                    raise RuntimeError('Storage space exhausted during conversion; source footage retained')
            await self.proc.wait()
            log.close()
            if self.closing:
                return
            if self.proc.returncode == 0:
                info = await self.probe(out)
                output_tracks = {stream['codec_type'] for stream in info['streams'] if stream['codec_type'] in {'video', 'audio'}}
                if output_tracks and output_tracks >= expected_tracks and float(info['format'].get('duration', 0)) > 0:
                    playable = await self.validate_decode(out, folder)
                    if self.closing:
                        return
                    if playable:
                        break
        else:
            raise RuntimeError('MP4 conversion failed or playback validation failed. Inspect conversion.log and validation.log; source footage retained.')
        out.replace(folder / r['filename'])
        r['state'] = 'ready'
        r['progress'] = 100
        self.save(r)
        for p in pieces:
            p.unlink()

def create_app(path=None):
    service = Recorder(path or os.environ.get('DATA_DIR', '/data'))
    app = web.Application()
    from recorder.auth import install
    access = install(app, service)
    app[web.AppKey('recorder', Recorder)] = service

    async def api(request):
        try:
            async with service.gate:
                route = request.match_info.get('tail', '')
                method = request.method
                allowed = {'settings': {'GET', 'PUT'}, 'status': {'GET'}, 'recordings': {'GET', 'POST'}, 'stop': {'POST'}}
                if route in allowed:
                    if method not in allowed[route]:
                        raise web.HTTPMethodNotAllowed(method, allowed[route])
                elif route.startswith('recordings/'):
                    path_parts = route.split('/')
                    if len(path_parts) == 2:
                        methods = {'GET', 'PATCH', 'DELETE'}
                    elif len(path_parts) == 3 and path_parts[2] in {'stop', 'download'}:
                        methods = {'POST'} if path_parts[2] == 'stop' else {'GET'}
                    else:
                        raise web.HTTPNotFound()
                    if method not in methods:
                        raise web.HTTPMethodNotAllowed(method, methods)
                else:
                    raise web.HTTPNotFound()
                body = await request.json() if request.can_read_body else {}
                if not isinstance(body, dict):
                    raise web.HTTPBadRequest(text='JSON object required')
                if route == 'settings':
                    if method == 'PUT':
                        try:
                            settings = update_source(service.settings, body)
                        except ValueError as error:
                            raise web.HTTPBadRequest(text=str(error)) from None
                        if 'storage_cap_bytes' in body:
                            settings['storage_cap_bytes'] = body['storage_cap_bytes']
                        url = settings['source_url']
                        cap = settings['storage_cap_bytes']
                        if not isinstance(url, str) or (url and (urlsplit(url).scheme not in {'http', 'https'} or not urlsplit(url).hostname or any(c in url for c in '\n\r'))):
                            raise web.HTTPBadRequest(text='Source must be a direct HTTP(S) stream URL')
                        if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1024 ** 2:
                            raise web.HTTPBadRequest(text='Cap must be an integer of at least 1048576 bytes')
                        service.settings = settings
                        service.relay.invalidate_preview()
                        service.db.execute('INSERT OR REPLACE INTO settings VALUES(1,?)', (json.dumps(settings),))
                        service.db.commit()
                        service.space(0)
                    visible = public_source(service.settings)
                    visible['auth_enabled'] = access.state['enabled']
                    visible['preview_url'] = service.relay.register(source_config(service.settings)) if visible['source_configured'] else ''
                    return web.json_response(visible)
                if route == 'status':
                    configured = bool(service.settings['source_url'])
                    return web.json_response({'active': public_record(service.active()), 'storage': service.usage(), 'source_configured': configured, 'preview_url': service.relay.register(source_config(service.settings)) if configured else ''})
                if route == 'recordings' and method == 'GET':
                    return web.json_response([public_record(r) for r in sorted(service.records.values(), key=lambda r: r['started_at'], reverse=True)])
                if route == 'recordings' and method == 'POST':
                    duration = body.get('duration_seconds')
                    if duration is not None and (isinstance(duration, bool) or not isinstance(duration, (float, int)) or (not 0 < duration <= 31536000)):
                        raise web.HTTPBadRequest(text='Duration must be positive seconds, at most one year')
                    if service.active():
                        raise web.HTTPConflict(text='Recorder busy capturing or processing')
                    if not service.settings['source_url']:
                        raise web.HTTPBadRequest(text='Configure a stream in Settings before starting a recording')
                    if not service.space(16 * 1024 ** 2):
                        raise web.HTTPInsufficientStorage(text='Insufficient cap or disk headroom; delete or unpin recordings')
                    now = dt.datetime.now(dt.timezone.utc)
                    ident = uuid.uuid4().hex
                    r = {'id': ident, 'state': 'recording', 'started_at': now.isoformat(), 'deadline': (now + dt.timedelta(seconds=duration)).isoformat() if duration else None, 'filename': now.strftime('%Y-%m-%d_%H-%M-%S_UTC') + '_' + ident[:8] + '.mp4', 'source_url': service.settings['source_url'], 'pinned': False, 'progress': 0, 'error': None, 'interruptions': [], 'stop_requested': False}
                    service.records[ident] = r
                    r['source_config'] = source_config(service.settings)
                    r.pop('source_url', None)
                    service.save(r)
                    service.task = asyncio.create_task(service.run(r))
                    return web.json_response(public_record(r), status=201)
                parts = route.split('/')
                ident = body.get('recording_id') if route == 'stop' else parts[1] if len(parts) > 1 else None
                if ident is not None and (not isinstance(ident, str) or not ident):
                    raise web.HTTPBadRequest(text='recording_id must be a nonempty string')
                r = service.records.get(ident) if ident else service.active()
                if route == 'stop' and r is None and (ident is None):
                    return web.json_response({'active': None})
                if r is None:
                    raise web.HTTPNotFound(text='Unknown recording')
                action = parts[2] if len(parts) > 2 else ''
                if route == 'stop' or action == 'stop':
                    r['stop_requested'] = True
                    service.save(r)
                    return web.json_response(public_record(r))
                if action == 'download':
                    if r['state'] != 'ready':
                        raise web.HTTPConflict(text='Recording is not ready')
                    return web.FileResponse(service.media / r['id'] / r['filename'], headers={'Content-Disposition': 'attachment; filename="' + r['filename'] + '"'})
                if method == 'PATCH':
                    if not isinstance(body.get('pinned'), bool):
                        raise web.HTTPBadRequest(text='pinned must be boolean')
                    r['pinned'] = body['pinned']
                    service.save(r)
                if method == 'DELETE':
                    if r['state'] in ACTIVE:
                        raise web.HTTPConflict(text='Recording media is in use')
                    service.remove(r)
                    return web.json_response({'deleted': ident})
                return web.json_response(public_record(r))
        except (json.JSONDecodeError, ValueError, TypeError):
            raise web.HTTPBadRequest(text='Invalid request JSON or values')

    async def health(request):
        return web.json_response({'ok': True})
    app.router.add_get('/api/health', health)
    app.router.add_route('*', '/api/{tail:.*}', api)
    app.router.add_get('/health', health)
    app.router.add_get('/preview/{token}', service.relay.handle)
    app.on_startup.append(service.startup)
    app.on_cleanup.append(service.shutdown)
    static = Path(__file__).parent / 'static'

    async def index(request):
        return web.FileResponse(static / 'index.html')
    app.router.add_get('/', index)
    if static.exists():
        app.router.add_static('/static/', static)
    return app
