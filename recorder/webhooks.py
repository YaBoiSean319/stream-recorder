"""Persistent signed webhook outbox with bounded deliveries and safe endpoints."""
import asyncio
import datetime as dt
import hashlib
import hmac
import ipaddress
import json
import socket
import time
import uuid
from urllib.parse import urlsplit

from aiohttp import ClientSession, ClientTimeout, TCPConnector, web
from aiohttp.abc import AbstractResolver

EVENTS = {'recording.completed', 'recording.failed', 'upload.completed', 'upload.failed'}
MAX_ATTEMPTS = 5


def endpoint(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value):
        raise ValueError('Webhook URL must be a valid HTTPS URL')
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        raise ValueError('Invalid webhook port') from None
    if parsed.scheme not in {'https', 'http'} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query or port == 0:
        raise ValueError('Webhook URL must be an HTTPS URL without credentials or fragment')
    hostname = parsed.hostname
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if parsed.scheme == 'http' and (not address or not address.is_loopback or hostname not in {'127.0.0.1', '::1'}):
        raise ValueError('HTTP webhooks require a local loopback receiver')
    if address and (not address.is_global and not address.is_loopback or address.is_loopback and parsed.scheme != 'http'):
        raise ValueError('Webhook addresses must be public, except an HTTP loopback receiver')
    return value


class PinnedResolver(AbstractResolver):
    def __init__(self, addresses): self.addresses = addresses
    async def resolve(self, host, port=0, family=socket.AF_INET):
        return [{'hostname':host,'host':str(address),'port':port,'family':socket.AF_INET6 if address.version == 6 else socket.AF_INET,'proto':0,'flags':0} for address in self.addresses]
    async def close(self): pass


async def resolve(url):
    hostname = urlsplit(url).hostname
    try:
        addresses = {ipaddress.ip_address(hostname)}
    except ValueError:
        loop = asyncio.get_running_loop()
        infos = await asyncio.wait_for(loop.getaddrinfo(hostname, urlsplit(url).port or 443, type=socket.SOCK_STREAM), 3)
        addresses = {ipaddress.ip_address(info[4][0]) for info in infos}
    if not addresses or any(not a.is_global and not (a.is_loopback and hostname in {'127.0.0.1','::1'} and url.startswith('http://')) for a in addresses):
        raise ValueError('Webhook destination resolved to an unsafe address')
    return addresses


class Webhooks:
    def __init__(self, service):
        self.service, self.db = service, service.db
        self.db.execute('CREATE TABLE IF NOT EXISTS webhook_destinations(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS webhook_deliveries(id TEXT PRIMARY KEY,destination_id TEXT,event_id TEXT,payload TEXT,status TEXT,attempts INTEGER,next_attempt REAL,last_error TEXT,created_at TEXT,UNIQUE(destination_id,event_id))')
        self.db.commit()
        self.destinations = {i:json.loads(p) for i,p in self.db.execute('SELECT id,payload FROM webhook_destinations')}
        self.closing = False
        self.worker = None
        service.webhooks = self

    def public(self, item):
        return {k:v for k,v in item.items() if k != 'secret'} | {'secret_saved':bool(item.get('secret'))}

    def emit(self, event, identity, data):
        if event not in EVENTS: return
        event_id = hashlib.sha256((event+':'+identity).encode()).hexdigest()
        payload = json.dumps({'id':event_id,'type':event,'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),'data':data},separators=(',',':'))
        with self.db:
            # A bounded outbox cannot consume unbounded disk during an unreachable destination.
            self.db.execute("DELETE FROM webhook_deliveries WHERE id IN (SELECT id FROM webhook_deliveries WHERE status IN ('delivered','failed','canceled') ORDER BY created_at DESC LIMIT -1 OFFSET 1000)")
            if self.db.execute("SELECT count(*) FROM webhook_deliveries WHERE status='pending'").fetchone()[0] >= 10000:
                return
            for destination in self.destinations.values():
                if not destination['enabled'] or event not in destination['events']: continue
                self.db.execute('INSERT OR IGNORE INTO webhook_deliveries VALUES(?,?,?,?,?,?,?,?,?)',
                    (uuid.uuid4().hex,destination['id'],event_id,payload,'pending',0,0,None,dt.datetime.now(dt.timezone.utc).isoformat()))

    def recording_changed(self, record, previous):
        state=record['state']
        if state == previous: return
        if state not in {'ready','error'}: return
        event='recording.completed' if state=='ready' else 'recording.failed'
        self.emit(event,record['id'],{'recording_id':record['id'],'state':state,'started_at':record.get('started_at'),'title':record.get('title'),'error':record.get('error') if state=='error' else None})

    def upload_changed(self, job, previous):
        state=job['state']
        if previous==state or state not in {'completed','failed','uncertain'}: return
        event='upload.completed' if state=='completed' else 'upload.failed'
        self.emit(event,job['id'],{'upload_id':job['id'],'recording_id':job['recording_id'],'state':state,'url':job.get('url') if state=='completed' else None,'error':job.get('error') if state!='completed' else None})

    async def startup(self, app):
        self.worker=asyncio.create_task(self.loop())

    async def shutdown(self, app):
        self.closing=True
        if self.worker:
            self.worker.cancel()
            try: await self.worker
            except asyncio.CancelledError: pass

    async def send(self, destination, event_id, payload):
        addresses=await resolve(destination['url'])
        timestamp=str(int(time.time()))
        body=payload.encode()
        signature=hmac.new(destination['secret'].encode(),timestamp.encode()+b'.'+body,hashlib.sha256).hexdigest()
        headers={'Content-Type':'application/json','X-Stream-Recorder-Event-Id':event_id,
                 'X-Stream-Recorder-Event':json.loads(payload)['type'],
                 'X-Stream-Recorder-Timestamp':timestamp,'X-Stream-Recorder-Signature':'sha256='+signature}
        connector=TCPConnector(resolver=PinnedResolver(addresses),use_dns_cache=False)
        async with ClientSession(connector=connector,timeout=ClientTimeout(total=10),trust_env=False) as client:
            async with client.post(destination['url'],data=body,headers=headers,allow_redirects=False) as response:
                if not 200<=response.status<300:
                    raise ValueError('Receiver returned HTTP '+str(response.status))

    async def loop(self):
        while not self.closing:
            row=self.db.execute("SELECT id,destination_id,event_id,payload,attempts FROM webhook_deliveries WHERE status='pending' AND next_attempt<=? ORDER BY created_at,id LIMIT 1",(time.time(),)).fetchone()
            if not row:
                await asyncio.sleep(.2)
                continue
            ident,destination_id,event_id,payload,attempts=row
            destination=self.destinations.get(destination_id)
            if not destination or not destination['enabled']:
                self.db.execute("UPDATE webhook_deliveries SET status='canceled' WHERE id=?",(ident,));self.db.commit();continue
            try:
                await self.send(destination,event_id,payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                attempts+=1
                # Never expose URLs (which may include tokens), secrets, response bodies, or transport errors.
                message='Delivery failed or destination unavailable'
                self.db.execute('UPDATE webhook_deliveries SET status=?,attempts=?,next_attempt=?,last_error=? WHERE id=?',
                    ('failed' if attempts>=MAX_ATTEMPTS else 'pending',attempts,time.time()+min(60,2**attempts),message,ident))
            else:
                self.db.execute("UPDATE webhook_deliveries SET status='delivered',attempts=?,last_error=NULL WHERE id=?",(attempts+1,ident))
            self.db.commit()

    async def routes(self, request):
        ident=request.match_info.get('destination_id')
        if request.path.endswith('/deliveries'):
            rows=self.db.execute('SELECT id,destination_id,event_id,payload,status,attempts,next_attempt,last_error,created_at FROM webhook_deliveries ORDER BY created_at DESC,id DESC LIMIT 200').fetchall()
            return web.json_response({'items':[dict(zip(('id','destination_id','event_id','event','status','attempts','next_attempt','last_error','created_at'),(r[0],r[1],r[2],json.loads(r[3])['type'],*r[4:]))) for r in rows]})
        if request.method=='GET':
            if ident:
                item=self.destinations.get(ident)
                if not item: raise web.HTTPNotFound(text='Unknown webhook')
                return web.json_response(self.public(item))
            return web.json_response({'items':[self.public(item) for item in self.destinations.values()]})
        if request.method=='DELETE':
            if ident not in self.destinations: raise web.HTTPNotFound(text='Unknown webhook')
            self.destinations.pop(ident)
            with self.db:
                self.db.execute('DELETE FROM webhook_destinations WHERE id=?',(ident,))
                self.db.execute("UPDATE webhook_deliveries SET status='canceled' WHERE destination_id=? AND status='pending'",(ident,))
            return web.json_response({'deleted':ident})
        body=await request.json()
        if not isinstance(body,dict) or set(body)-{'url','events','secret','enabled'}: raise web.HTTPBadRequest(text='Unknown webhook fields')
        current=self.destinations.get(ident) if ident else None
        if ident and not current: raise web.HTTPNotFound(text='Unknown webhook')
        item=dict(current or {'id':uuid.uuid4().hex,'enabled':True})
        item.update(body)
        try:
            item['url']=endpoint(item['url'])
        except (ValueError,KeyError) as exc: raise web.HTTPBadRequest(text='Invalid webhook URL') from None
        if not isinstance(item.get('events'),list) or not item['events'] or any(not isinstance(e,str) or e not in EVENTS for e in item['events']) or len(item['events']) != len(set(item['events'])):
            raise web.HTTPBadRequest(text='Select supported webhook events')
        if not isinstance(item.get('secret'),str) or not 16<=len(item['secret'])<=256:
            raise web.HTTPBadRequest(text='Signing secret must contain 16–256 characters')
        if not isinstance(item['enabled'],bool): raise web.HTTPBadRequest(text='enabled must be boolean')
        self.destinations[item['id']]=item
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO webhook_destinations VALUES(?,?)',(item['id'],json.dumps(item)))
            if not item['enabled']:
                self.db.execute("UPDATE webhook_deliveries SET status='canceled' WHERE destination_id=? AND status='pending'",(item['id'],))
        return web.json_response(self.public(item),status=201 if not ident else 200,headers={'Cache-Control':'no-store'})


def install(app, service):
    webhooks=Webhooks(service)
    app.router.add_get('/api/v1/webhooks/deliveries',webhooks.routes)
    app.router.add_get('/api/v1/webhooks',webhooks.routes)
    app.router.add_post('/api/v1/webhooks',webhooks.routes)
    app.router.add_get('/api/v1/webhooks/{destination_id}',webhooks.routes)
    app.router.add_put('/api/v1/webhooks/{destination_id}',webhooks.routes)
    app.router.add_delete('/api/v1/webhooks/{destination_id}',webhooks.routes)
    app.on_startup.append(webhooks.startup)
    app.on_cleanup.insert(0,webhooks.shutdown)
    return webhooks
