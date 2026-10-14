"""Durable, single-worker resumable YouTube uploads."""
import asyncio
import hashlib
import json
import random
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from aiohttp import web

from .server import stamp

CHUNK = 8 * 1024 * 1024
PROTECT = {'queued', 'uploading', 'paused', 'failed', 'uncertain'}
RETRYABLE = {408, 429, 500, 502, 503, 504}


def public(job):
    return {k: v for k, v in job.items() if k not in {'session_uri', 'fingerprint', 'file_name'}}


class Uploads:
    def __init__(self, service, youtube):
        self.service, self.youtube, self.db = service, youtube, service.db
        self.db.execute('CREATE TABLE IF NOT EXISTS youtube_uploads(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS youtube_upload_keys(caller TEXT,key TEXT,fingerprint TEXT,job_id TEXT,PRIMARY KEY(caller,key))')
        self.db.commit()
        self.jobs = {i:json.loads(p) for i,p in self.db.execute('SELECT id,payload FROM youtube_uploads')}
        self.lock = asyncio.Lock()
        self.worker = None
        self.closing = False
        self.active_job = None
        service.uploads = self

    def save(self, job):
        job['updated_at'] = stamp()
        self.db.execute('INSERT OR REPLACE INTO youtube_uploads VALUES(?,?)',(job['id'],json.dumps(job)))
        self.db.commit()

    def protected(self, ident):
        return any(j['recording_id']==ident and (j['state'] in PROTECT or j['id']==self.active_job) for j in self.jobs.values())

    async def startup(self, app):
        for job in self.jobs.values():
            if job['state']=='uploading':
                job['state']='queued'; self.save(job)
        self.worker=asyncio.create_task(self.loop(app))

    async def shutdown(self, app):
        self.closing=True
        if self.worker:
            self.worker.cancel()
            try: await self.worker
            except asyncio.CancelledError: pass

    def connected(self, job):
        data=self.youtube.data
        return bool(data.get('channel') and not data.get('reconnect_required') and data.get('token') and data['channel']['id']==job['channel_id'])

    async def access_token(self, app):
        token=self.youtube.data.get('token') or {}
        if token.get('expires_at',0)<time.time()+60:
            class Request:
                def __init__(self, app):self.app=app
            try: await self.youtube.refresh(Request(app))
            except web.HTTPException as exc: raise PauseUpload(str(exc)) from None
            token=self.youtube.data['token']
        return token['access_token']

    def endpoints(self, app):
        return app.get('youtube_provider', {}).get('upload','https://www.googleapis.com/upload/youtube/v3/videos')

    def check_session(self, uri, app):
        parsed=urlsplit(uri)
        hosts=app.get('youtube_provider',{}).get('session_hosts')
        if hosts is not None:
            if parsed.scheme not in {'http','https'} or parsed.hostname not in hosts: raise ValueError('Unsafe YouTube upload session')
        elif parsed.scheme!='https' or parsed.hostname not in {'www.googleapis.com','youtube.googleapis.com'}:
            raise ValueError('Unsafe YouTube upload session')

    def path(self, job):
        record=self.service.records.get(job['recording_id'])
        path=self.service.media/job['recording_id']/job['file_name']
        if not record or record['state']!='ready' or not path.is_file() or path.stat().st_size!=job['size']:
            raise ValueError('Recording media changed or is unavailable')
        return path

    def response(self, job, response):
        if response.status_code in {200,201}:
            remote=response.json().get('id')
            if not isinstance(remote,str) or not remote:raise ValueError('YouTube did not provide a video ID')
            job.update({'state':'completed','video_id':remote,'url':'https://www.youtube.com/watch?v='+remote,'acknowledged_bytes':job['size'],'error':None})
            self.save(job)
            return True
        if response.status_code==308:
            part=response.headers.get('Range','')
            if part:
                try: offset=int(part.rsplit('-',1)[1])+1
                except ValueError: raise ValueError('Invalid YouTube upload offset') from None
                if offset>job['size']:raise ValueError('Invalid YouTube upload offset')
                job['acknowledged_bytes']=offset
            else:job['acknowledged_bytes']=0
            self.save(job)
            return False
        if response.status_code==404:
            raise UncertainUpload('Upload session expired; remote outcome cannot be verified. Check the channel before explicit requeue.')
        if response.status_code in RETRYABLE:raise RetryUpload('YouTube temporarily unavailable',response.headers.get('Retry-After'))
        if response.status_code in {401,403}:raise PauseUpload('YouTube authorization or quota needs attention; reconnect or review Google response')
        raise ValueError(f'YouTube rejected upload (HTTP {response.status_code})')

    async def transfer(self, job, app):
        path=self.path(job)
        token=await self.access_token(app)
        headers={'Authorization':'Bearer '+token}
        async with httpx.AsyncClient(timeout=httpx.Timeout(60,read=120),trust_env=False,follow_redirects=False) as http:
            if not job.get('session_uri'):
                data=job['metadata']
                body={'snippet':{'title':data['title'],'description':data['description'],'tags':data['tags'],'categoryId':data['category_id']},'status':{'privacyStatus':data['privacy_status'],'selfDeclaredMadeForKids':data['made_for_kids']}}
                response=await http.post(self.endpoints(app),params={'uploadType':'resumable','part':'snippet,status','notifySubscribers':str(data['notify_subscribers']).lower()},json=body,headers={**headers,'X-Upload-Content-Length':str(job['size']),'X-Upload-Content-Type':'video/mp4'})
                if response.status_code in RETRYABLE:raise RetryUpload('Could not start YouTube upload',response.headers.get('Retry-After'))
                if response.status_code not in {200,201}:
                    self.response(job,response)
                    raise ValueError(f'Could not start YouTube upload (HTTP {response.status_code})')
                uri=response.headers.get('Location','')
                self.check_session(uri,app)
                job['session_uri']=uri
                self.save(job) # commit remote session before first media byte
            uri=job['session_uri']
            self.check_session(uri,app)
            # Probe every persisted session, including after a lost final response.
            try:
                probe=await http.put(uri,headers={**headers,'Content-Length':'0','Content-Range':f'bytes */{job["size"]}'},content=b'')
            except httpx.HTTPError as exc:raise RetryUpload('Upload probe interrupted') from exc
            if self.response(job,probe):return
            while job['acknowledged_bytes']<job['size']:
                if job['state']!='uploading' or not self.connected(job):raise PauseUpload('Upload paused')
                start=job['acknowledged_bytes'];end=min(start+CHUNK,job['size'])
                with path.open('rb') as media:
                    media.seek(start);content=media.read(end-start)
                try:
                    response=await http.put(uri,headers={**headers,'Content-Type':'video/mp4','Content-Length':str(len(content)),'Content-Range':f'bytes {start}-{end-1}/{job["size"]}'},content=content)
                except httpx.HTTPError as exc:raise RetryUpload('Upload interrupted; probing saved session before retry') from exc
                if self.response(job,response):return
                if job['acknowledged_bytes']<=start:raise RetryUpload('YouTube reported no upload progress')
            raise RetryUpload('Checking completed upload with saved session')

    async def loop(self, app):
        while not self.closing:
            for pending in self.jobs.values():
                if pending['state']=='queued' and not self.connected(pending):
                    pending.update({'state':'paused','pause_reason':'connection','error':'Reconnect the original YouTube channel to resume, or explicitly requeue for a different channel'})
                    self.save(pending)
                elif pending['state']=='paused' and pending.get('pause_reason')=='connection' and self.connected(pending):
                    pending.update({'state':'queued','pause_reason':None,'error':None})
                    self.save(pending)
            job=next((j for j in sorted(self.jobs.values(),key=lambda x:x['created_at']) if j['state']=='queued' and j.get('next_attempt',0)<=time.time() and self.connected(j)),None)
            if job is None:
                await asyncio.sleep(.2);continue
            async with self.lock:
                if job['state']!='queued':continue
                job['state']='uploading';job['attempts']+=1;self.active_job=job['id'];self.save(job)
                try:await self.transfer(job,app)
                except asyncio.CancelledError:
                    job['state']='queued' if job['state']=='uploading' else job['state'];self.save(job);raise
                except PauseUpload as exc:
                    if job['state']=='uploading':job.update({'state':'paused','error':str(exc)});self.save(job)
                except UncertainUpload as exc:
                    job.update({'state':'uncertain','error':str(exc)});self.save(job)
                except RetryUpload as exc:
                    if job['state']!='uploading':continue
                    if job['attempts']>=5:
                        job.update({'state':'failed','error':str(exc)+'; retry to probe saved session'})
                    else:
                        delay=min(60,2**job['attempts']+random.random())
                        if exc.retry_after and exc.retry_after.isdigit():delay=min(300,max(delay,int(exc.retry_after)))
                        job.update({'state':'queued','next_attempt':time.time()+delay,'error':str(exc)})
                    self.save(job)
                except (ValueError,httpx.HTTPError) as exc:
                    if job['state']=='uploading':job.update({'state':'failed','error':str(exc)});self.save(job)
                finally:
                    self.active_job=None

    def metadata(self, body, record):
        if not isinstance(body,dict) or set(body)-{'title','description','privacy_status','made_for_kids','notify_subscribers','tags','category_id'}:raise web.HTTPBadRequest(text='Invalid upload metadata')
        if 'made_for_kids' not in body or not isinstance(body['made_for_kids'],bool):raise web.HTTPBadRequest(text='Explicit made_for_kids choice required')
        title=body.get('title') or record.get('title') or record['filename'].removesuffix('.mp4')
        description=body.get('description','');tags=body.get('tags',[]);category=body.get('category_id','22')
        privacy=body.get('privacy_status','unlisted');notify=body.get('notify_subscribers',False)
        if not isinstance(title,str) or not 1<=len(title)<=100 or not isinstance(description,str) or len(description)>5000 or privacy not in {'private','unlisted','public'} or not isinstance(notify,bool) or not isinstance(tags,list) or len(tags)>50 or any(not isinstance(t,str) or not t or len(t)>100 for t in tags) or not isinstance(category,str) or not category.isdigit():raise web.HTTPBadRequest(text='Invalid YouTube title, description, visibility, tags, category, or notification setting')
        return {'title':title,'description':description,'privacy_status':privacy,'made_for_kids':body['made_for_kids'],'notify_subscribers':notify,'tags':tags,'category_id':category}

    async def channel(self, request):
        channel=self.youtube.data.get('channel')
        return web.json_response({'channel':channel,'connected':bool(channel and not self.youtube.data.get('reconnect_required') and self.youtube.data.get('token'))})

    async def routes(self, request):
        method=request.method;id=request.match_info.get('job_id');record_id=request.match_info.get('recording_id')
        async with self.service.gate:
            if method=='GET':
                if id:
                    job=self.jobs.get(id)
                    if not job:raise web.HTTPNotFound(text='Unknown upload')
                    return web.json_response(public(job))
                jobs=[public(j) for j in sorted(self.jobs.values(),key=lambda j:j['created_at'],reverse=True) if record_id is None or j['recording_id']==record_id]
                return web.json_response({'items':jobs})
            if method=='POST' and not id:
                record=self.service.records.get(record_id)
                if not record or record['state']!='ready':raise web.HTTPConflict(text='A finalized recording is required')
                channel=self.youtube.data.get('channel')
                if not channel or self.youtube.data.get('reconnect_required') or not self.youtube.data.get('token'):raise web.HTTPConflict(text='Connect a YouTube channel first')
                path=self.service.media/record_id/record['filename']
                if not path.is_file() or not path.stat().st_size:raise web.HTTPConflict(text='Final media unavailable')
                body=await request.json();meta=self.metadata(body,record)
                key=request.headers.get('Idempotency-Key');caller=request.get('caller_id','local')
                if key is not None and (not 1<=len(key)<=200 or any(ord(c)<33 or ord(c)>126 for c in key)):raise web.HTTPBadRequest(text='Invalid Idempotency-Key')
                fingerprint=hashlib.sha256(json.dumps({'recording_id':record_id,'metadata':meta},sort_keys=True).encode()).hexdigest()
                if key:
                    prior=self.db.execute('SELECT fingerprint,job_id FROM youtube_upload_keys WHERE caller=? AND key=?',(caller,key)).fetchone()
                    if prior:
                        if prior[0]!=fingerprint:raise web.HTTPConflict(text='Idempotency key reused with different upload')
                        return web.json_response(public(self.jobs[prior[1]]),status=201,headers={'Idempotency-Replayed':'true'})
                job={'id':uuid.uuid4().hex,'recording_id':record_id,'file_name':record['filename'],'size':path.stat().st_size,'metadata':meta,'channel_id':channel['id'],'channel_name':channel['name'],'state':'queued','attempts':0,'acknowledged_bytes':0,'session_uri':None,'video_id':None,'url':None,'error':None,'created_at':stamp(),'updated_at':stamp()}
                self.jobs[job['id']]=job
                with self.db:
                    self.db.execute('INSERT INTO youtube_uploads VALUES(?,?)',(job['id'],json.dumps(job)))
                    if key:self.db.execute('INSERT INTO youtube_upload_keys VALUES(?,?,?,?)',(caller,key,fingerprint,job['id']))
                return web.json_response(public(job),status=201)
            job=self.jobs.get(id)
            if not job:raise web.HTTPNotFound(text='Unknown upload')
            action=request.match_info['action']
            if action=='cancel':
                if job['state'] not in {'completed','canceled'}:job.update({'state':'canceled','error':None});self.save(job)
            elif action=='release':
                if job['state'] not in {'failed','uncertain','paused'}:raise web.HTTPConflict(text='Only stopped uploads can release media')
                job.update({'state':'released','error':'Local media protection released; remote status may still need reconciliation'});self.save(job)
            elif action=='retry':
                if job['state'] not in {'failed','paused'}:raise web.HTTPConflict(text='Only failed or paused uploads can retry')
                if not self.connected(job):raise web.HTTPConflict(text='Reconnect the original channel before retry')
                job.update({'state':'queued','pause_reason':None,'attempts':0,'next_attempt':0,'error':None});self.save(job)
            elif action=='requeue':
                if job['state'] not in {'completed','canceled','released','failed','uncertain','paused'}:raise web.HTTPConflict(text='Stop this upload before explicit requeue')
                if not self.youtube.data.get('channel'):raise web.HTTPConflict(text='Connect a channel first')
                if job['state']=='uncertain' and not (await request.json()).get('reconciled'):
                    raise web.HTTPConflict(text='Verify the channel and confirm reconciled=true before creating another remote video')
                record=self.service.records.get(job['recording_id'])
                if not record or record['state']!='ready':raise web.HTTPConflict(text='Recording media unavailable')
                path=self.service.media/job['recording_id']/record['filename']
                if not path.is_file():raise web.HTTPConflict(text='Recording media unavailable')
                new=dict(job,id=uuid.uuid4().hex,file_name=record['filename'],size=path.stat().st_size,channel_id=self.youtube.data['channel']['id'],channel_name=self.youtube.data['channel']['name'],session_uri=None,acknowledged_bytes=0,state='queued',attempts=0,video_id=None,url=None,error=None,created_at=stamp(),updated_at=stamp())
                self.jobs[new['id']]=new;self.save(new)
                return web.json_response(public(new),status=201)
            else:raise web.HTTPNotFound()
            return web.json_response(public(job))

class RetryUpload(Exception):
    def __init__(self,message,retry_after=None):super().__init__(message);self.retry_after=retry_after
class PauseUpload(Exception):pass
class UncertainUpload(Exception):pass


def install(app,service,youtube):
    uploads=Uploads(service,youtube)
    app.router.add_get('/api/v1/uploads',uploads.routes)
    app.router.add_get('/api/v1/uploads/channel',uploads.channel)
    app.router.add_get('/api/v1/uploads/{job_id}',uploads.routes)
    app.router.add_post('/api/v1/uploads/{job_id}/{action:retry|cancel|requeue|release}',uploads.routes)
    app.router.add_get('/api/v1/recordings/{recording_id}/uploads',uploads.routes)
    app.router.add_post('/api/v1/recordings/{recording_id}/uploads',uploads.routes)
    app.on_startup.append(uploads.startup)
    app.on_cleanup.insert(0,uploads.shutdown)
    return uploads
