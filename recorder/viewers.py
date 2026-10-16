"""Anonymous presence collection for an Administrator-configured external player."""
import hashlib
import ipaddress
import json
import math
import re
import secrets
import time
import asyncio
import uuid
from urllib.parse import urlsplit

from aiohttp import web

ID = re.compile(r"^[A-Za-z0-9_-]{16,80}$")
SAMPLE_INTERVAL = 30


def origin(value):
    parsed = urlsplit(value) if isinstance(value, str) else None
    try:
        port = parsed.port if parsed else None
    except ValueError:
        return None
    if not parsed or parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return None
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if port and not (parsed.scheme == "https" and port == 443) and not (parsed.scheme == "http" and port == 80):
        host = f"{host}:{port}"
    return f"{parsed.scheme.lower()}://{host}"


def web_url(value, *, allow_http=False):
    parsed = urlsplit(value) if isinstance(value, str) else None
    try:
        parsed.port if parsed else None
    except ValueError:
        return False
    if not parsed or parsed.scheme not in ({"https", "http"} if allow_http else {"https"}) or not parsed.hostname or parsed.username or parsed.password or any(c in value for c in "\r\n"):
        return False
    if allow_http and parsed.scheme == "http":
        host = parsed.hostname.lower()
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = host == "localhost"
        if not local:
            return False
    return True


class Viewers:
    def __init__(self, service):
        self.service = service
        self.db = service.db
        self.db.execute("CREATE TABLE IF NOT EXISTS viewer_config(id INTEGER PRIMARY KEY,payload TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS viewer_sessions(browser_id TEXT NOT NULL,tab_id TEXT NOT NULL,reported_at REAL NOT NULL,PRIMARY KEY(browser_id,tab_id))")
        self.db.execute("CREATE TABLE IF NOT EXISTS viewer_samples(sampled_at REAL PRIMARY KEY,count INTEGER,configuration_id TEXT NOT NULL,covered INTEGER NOT NULL)")
        self.db.commit()
        self.rate = {}
        row = self.db.execute("SELECT payload FROM viewer_config WHERE id=1").fetchone()
        self.config = json.loads(row[0]) if row else {"player_url":"", "allowed_origin":"", "public_tracking_url":"", "timezone":"UTC", "timezone_configured":False, "enabled":False, "tracking_hash":"", "last_report_at":None}
        self.config.setdefault("timezone_configured", True)
        self.config.setdefault("configuration_id", str(uuid.uuid4()))
        # Persisted history cannot prove that this process's collector is healthy.
        self.last_sample_at = None
        self.key_for_response = ""
        self._save()

    def _save(self):
        safe = {k:v for k,v in self.config.items() if k != "tracking_key_once"}
        self.db.execute("INSERT OR REPLACE INTO viewer_config VALUES(1,?)", (json.dumps(safe),))
        self.db.commit()

    def _snippet(self, key=""):
        cfg = self.config
        if not cfg["enabled"] or not cfg["tracking_hash"] or not cfg["public_tracking_url"]:
            return ""
        if not key:
            return ""
        endpoint = cfg["public_tracking_url"].rstrip("/") + "/api/v1/viewers/report"
        # The key is deliberately returned only during initial configuration or rotation.
        return f'''<script>(function(){{
const endpoint={json.dumps(endpoint)}, key={json.dumps(key)};
const video=document.querySelector('video'); if(!video)return;
const get=(k,make)=>{{let v;try{{v=localStorage.getItem(k);if(!v){{v=make();localStorage.setItem(k,v)}}}}catch(_){{v=make()}}return v}};
const id=()=>Array.from(crypto.getRandomValues(new Uint8Array(18)),b=>b.toString(16).padStart(2,'0')).join('');
const browser=get('stream-recorder-browser-id',id), tab=id();
let lastTime=video.currentTime, progressed=false, timer;
function send(playing){{fetch(endpoint,{{method:'POST',mode:'cors',keepalive:true,headers:{{'Content-Type':'application/json','X-Tracking-Key':key}},body:JSON.stringify({{browser_id:browser,tab_id:tab,playing}})}}).catch(()=>{{}})}}
function stop(){{clearInterval(timer);timer=undefined;send(false)}}
function tick(){{const now=video.currentTime; progressed=progressed||Math.abs(now-lastTime)>0.01;lastTime=now;send(!video.paused&&!video.ended&&!video.seeking&&progressed);progressed=false}}
video.addEventListener('timeupdate',()=>{{if(Math.abs(video.currentTime-lastTime)>0.01)progressed=true}});
for(const event of ['pause','ended','error','emptied','stalled','abort'])video.addEventListener(event,stop);
video.addEventListener('playing',()=>{{lastTime=video.currentTime;progressed=false;clearInterval(timer);timer=setInterval(tick,30000)}});
video.addEventListener('waiting',stop); window.addEventListener('pagehide',stop);
if(!video.paused&&!video.ended){{progressed=false;timer=setInterval(tick,30000)}}
}})();</script>'''

    def _visible(self, expose_key=False):
        c = self.config
        key = self.key_for_response if expose_key else ""
        return {"player_url":c["player_url"], "allowed_origin":c["allowed_origin"], "public_tracking_url":c["public_tracking_url"], "timezone":c["timezone"], "timezone_configured":c["timezone_configured"], "enabled":c["enabled"], "tracking_key_configured":bool(c["tracking_hash"]), "collector_health":"healthy", "last_report_at":c["last_report_at"], "snippet":self._snippet(key), **({"tracking_key":key} if expose_key and key else {})}

    async def config_route(self, request):
        if request.method == "GET":
            return web.json_response(self._visible())
        body = await request.json()
        if not isinstance(body, dict) or set(body) - {"player_url","allowed_origin","public_tracking_url","timezone","enabled"}:
            raise web.HTTPBadRequest(text="Unknown viewer configuration fields")
        updated = {**self.config, **body}
        if "timezone" in body:
            updated["timezone_configured"] = True
        if not isinstance(updated["player_url"], str) or (updated["player_url"] and not web_url(updated["player_url"], allow_http=True)):
            raise web.HTTPBadRequest(text="Player URL must be a valid HTTP(S) URL")
        parsed_player = urlsplit(updated["player_url"]) if updated["player_url"] else None
        expected_origin = origin(updated["allowed_origin"])
        if updated["allowed_origin"] and (not expected_origin or (parsed_player and expected_origin != origin(f"{parsed_player.scheme}://{parsed_player.netloc}"))):
            raise web.HTTPBadRequest(text="Allowed origin must match the configured player URL origin")
        if not isinstance(updated["public_tracking_url"], str) or (updated["public_tracking_url"] and not web_url(updated["public_tracking_url"], allow_http=True)):
            raise web.HTTPBadRequest(text="Public tracking URL must be a valid HTTP(S) base URL")
        if not isinstance(updated["timezone"], str) or len(updated["timezone"]) > 100:
            raise web.HTTPBadRequest(text="Timezone must be a valid timezone name")
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(updated["timezone"])
        except Exception:
            raise web.HTTPBadRequest(text="Unknown IANA reporting timezone") from None
        if not isinstance(updated["enabled"], bool):
            raise web.HTTPBadRequest(text="enabled must be boolean")
        if updated["enabled"] and not all((updated["player_url"], updated["allowed_origin"], updated["public_tracking_url"])):
            raise web.HTTPBadRequest(text="Player URL, allowed origin, and public tracking URL are required when enabled")
        if updated["enabled"] != self.config["enabled"]:
            self.db.execute("DELETE FROM viewer_sessions")
        # A changed player configuration gets a fresh anonymous cohort and key.
        changed = any(updated[k] != self.config[k] for k in ("player_url", "allowed_origin", "public_tracking_url"))
        if changed:
            self.db.execute("DELETE FROM viewer_sessions")
            updated["tracking_hash"] = ""
            updated["configuration_id"] = str(uuid.uuid4())
        self.config = updated
        key = None
        if self.config["enabled"] and not self.config["tracking_hash"]:
            key = secrets.token_urlsafe(32)
            self.config["tracking_hash"] = hashlib.sha256(key.encode()).hexdigest()
        if not updated["enabled"]:
            self.db.execute("DELETE FROM viewer_sessions")
        self.key_for_response = key or ""
        self._save()
        result = self._visible(expose_key=True)
        self.key_for_response = ""
        return web.json_response(result, headers={"Cache-Control":"no-store"})

    async def rotate(self, request):
        if not self.config["enabled"]:
            raise web.HTTPConflict(text="Enable viewer tracking before creating a key")
        key = secrets.token_urlsafe(32)
        self.config["tracking_hash"] = hashlib.sha256(key.encode()).hexdigest()
        self.key_for_response = key
        self.db.execute("DELETE FROM viewer_sessions")
        self._save()
        response = {"tracking_key":key,"tracking_key_configured":True,"snippet":self._snippet(key)}
        self.key_for_response = ""
        return web.json_response(response, headers={"Cache-Control":"no-store"})

    async def report(self, request):
        if request.method == "OPTIONS":
            response = web.Response(status=204)
        else:
            if request.content_length is not None and request.content_length > 4096:
                raise web.HTTPRequestEntityTooLarge(max_size=4096, actual_size=request.content_length)
            ip = request.remote or "unknown"
            now = time.time()
            if len(self.rate) > 2048:
                self.rate = {address:stamps for address,stamps in self.rate.items() if stamps and now-stamps[-1] < 60}
            recent = [stamp for stamp in self.rate.get(ip, []) if now - stamp < 60]
            if ip not in self.rate and len(self.rate) >= 2048:
                raise web.HTTPTooManyRequests(text="Tracking client capacity reached")
            if len(recent) >= 60:
                raise web.HTTPTooManyRequests(text="Tracking rate limit exceeded")
            recent.append(now)
            self.rate[ip] = recent
            supplied = request.headers.get("X-Tracking-Key", "")
            supplied_hash = hashlib.sha256(supplied.encode()).hexdigest()
            if not self.config["enabled"] or not self.config["tracking_hash"] or not secrets.compare_digest(supplied_hash, self.config["tracking_hash"]):
                raise web.HTTPUnauthorized(text="Invalid or revoked tracking key")
            request_origin = origin(request.headers.get("Origin", ""))
            if not request_origin or request_origin != self.config["allowed_origin"]:
                raise web.HTTPForbidden(text="Origin is not allowed")
            raw = await request.content.read(4097)
            if len(raw) > 4096:
                raise web.HTTPRequestEntityTooLarge(max_size=4096, actual_size=len(raw))
            body = json.loads(raw)
            if not isinstance(body, dict) or set(body) != {"browser_id","tab_id","playing"} or not all(isinstance(body.get(k), str) and ID.fullmatch(body[k]) for k in ("browser_id","tab_id")) or not isinstance(body.get("playing"), bool):
                raise web.HTTPBadRequest(text="Expected bounded browser_id, tab_id, and boolean playing")
            browser, tab = body["browser_id"], body["tab_id"]
            self.db.execute("DELETE FROM viewer_sessions WHERE reported_at<=?", (now - 90,))
            if body["playing"]:
                active = self.db.execute("SELECT COUNT(*) FROM viewer_sessions WHERE reported_at>?", (now - 90,)).fetchone()[0]
                existing = self.db.execute("SELECT 1 FROM viewer_sessions WHERE browser_id=? AND tab_id=?", (browser, tab)).fetchone()
                if active >= 10000 and not existing:
                    raise web.HTTPTooManyRequests(text="Active session capacity reached")
                self.db.execute("INSERT OR REPLACE INTO viewer_sessions VALUES(?,?,?)", (browser, tab, now))
            else:
                self.db.execute("DELETE FROM viewer_sessions WHERE browser_id=? AND tab_id=?", (browser, tab))
            self.config["last_report_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
            self._save()
            self.db.commit()
            response = web.json_response({"accepted":True}, status=202)
        response.headers.update({"Access-Control-Allow-Origin":self.config["allowed_origin"], "Access-Control-Allow-Headers":"Content-Type, X-Tracking-Key", "Access-Control-Allow-Methods":"POST, OPTIONS", "Vary":"Origin", "Cache-Control":"no-store"})
        return response

    async def live(self, request):
        if not self.config["enabled"]:
            return web.json_response({"state":"disabled","count":None,"as_of":None,"collector_health":self.collector_health(),"last_report_at":self.config["last_report_at"]})
        now = time.time()
        self.db.execute("DELETE FROM viewer_sessions WHERE reported_at<=?", (now - 90,))
        count = self.db.execute("SELECT COUNT(DISTINCT browser_id) FROM viewer_sessions WHERE reported_at>?", (now - 90,)).fetchone()[0]
        return web.json_response({"state":"available","count":count,"as_of":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime(now)),"collector_health":self.collector_health(),"last_report_at":self.config["last_report_at"],"expires_after_seconds":90})

    def collector_health(self):
        return "healthy" if self.last_sample_at is not None and time.time() - self.last_sample_at <= SAMPLE_INTERVAL * 2 else "unavailable"

    async def sample_once(self, now=None):
        """Persist one durable point; unavailable collection is an explicit gap."""
        now = time.time() if now is None else now
        covered = bool(self.config["enabled"])
        count = None
        if covered:
            self.db.execute("DELETE FROM viewer_sessions WHERE reported_at<=?", (now - 90,))
            count = self.db.execute("SELECT COUNT(DISTINCT browser_id) FROM viewer_sessions WHERE reported_at>?", (now - 90,)).fetchone()[0]
        self.db.execute("INSERT OR REPLACE INTO viewer_samples VALUES(?,?,?,?)", (now, count, self.config["configuration_id"], int(covered)))
        self.db.commit()
        self.last_sample_at = now

    async def collect(self, app):
        while True:
            try:
                await self.sample_once()
            except Exception:
                # A failed write must remain an unknown interval, never a fabricated zero.
                pass
            await asyncio.sleep(SAMPLE_INTERVAL)

    async def start(self, app):
        self.collector_task = asyncio.create_task(self.collect(app))

    async def stop(self, app):
        task = getattr(self, "collector_task", None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def parse_range(self, request):
        try:
            start, end = float(request.query["from"]), float(request.query["to"])
        except (KeyError, ValueError):
            raise web.HTTPBadRequest(text="from and to must be Unix timestamps") from None
        now = time.time()
        if not (0 < start < end <= now + 60) or end - start > 90 * 86400:
            raise web.HTTPBadRequest(text="Range must be increasing and no longer than 90 days")
        return start, end

    async def history(self, request):
        start, end = self.parse_range(request)
        now = time.time()
        try:
            resolution = request.query.get("resolution", "auto")
            if resolution not in {"auto", "30s", "1m", "5m", "15m", "1h", "6h", "1d"}:
                raise ValueError
            config_id = request.query.get("configuration_id", self.config["configuration_id"])
            if config_id is not None and (len(config_id) > 64 or not re.fullmatch(r"[a-fA-F0-9-]+", config_id)):
                raise ValueError
        except ValueError:
            raise web.HTTPBadRequest(text="Invalid resolution or configuration_id") from None
        step = {"30s":30,"1m":60,"5m":300,"15m":900,"1h":3600,"6h":21600,"1d":86400}.get(resolution)
        if step is None:
            step = max(SAMPLE_INTERVAL, (end-start) / 500)
        step = max(1, math.ceil(max(step, (end-start) / 1000)))
        rows = self.db.execute("SELECT sampled_at,count,configuration_id,covered FROM viewer_samples WHERE sampled_at<? AND sampled_at>=? AND configuration_id=? ORDER BY sampled_at", (end, start - SAMPLE_INTERVAL, config_id)).fetchall()
        buckets = {}
        covered_seconds = 0.0
        weighted = 0.0
        viewer_hours = 0.0
        peak = 0
        for i, row in enumerate(rows):
            stamp, count, identity, covered = row
            if not covered:
                continue
            next_stamp = rows[i+1][0] if i+1 < len(rows) and rows[i+1][2] == identity else stamp + SAMPLE_INTERVAL
            if i+1 == len(rows) and now - stamp > SAMPLE_INTERVAL * 1.5:
                continue
            duration = min(end, stamp + SAMPLE_INTERVAL, next_stamp) - max(start, stamp)
            if duration <= 0 or next_stamp - stamp > SAMPLE_INTERVAL * 1.5:
                continue
            bucket = int(stamp // step * step)
            item = buckets.setdefault(bucket, {"timestamp":bucket,"average":0,"peak":0,"covered_seconds":0,"_weighted":0})
            item["_weighted"] += count * duration
            item["covered_seconds"] += duration
            item["peak"] = max(item["peak"], count)
            covered_seconds += duration
            weighted += count * duration
            viewer_hours += count * duration / 3600
            peak = max(peak, count)
        points = []
        for item in sorted(buckets.values(), key=lambda x:x["timestamp"]):
            if item["covered_seconds"]:
                item["average"] = item["_weighted"] / item["covered_seconds"]
            del item["_weighted"]
            points.append(item)
        by_bucket = {item["timestamp"]:item for item in points}
        first_bucket = int(start // step * step)
        last_bucket = int((end - 1e-6) // step * step)
        points = []
        for bucket in range(first_bucket, last_bucket + 1, step):
            expected = max(0, min(end, bucket + step) - max(start, bucket))
            point = by_bucket.get(bucket, {"timestamp":bucket,"average":None,"peak":None,"covered_seconds":0})
            point["expected_seconds"] = expected
            point["complete"] = point["covered_seconds"] >= expected - 0.01
            points.append(point)
        live = await self.live(request)
        live_data = json.loads(live.body)
        configurations = [{"id":r[0],"first_at":r[1],"last_at":r[2]} for r in self.db.execute("SELECT configuration_id,MIN(sampled_at),MAX(sampled_at) FROM viewer_samples GROUP BY configuration_id ORDER BY MAX(sampled_at) DESC")]
        return web.json_response({"from":start,"to":end,"timezone":self.config["timezone"],"configuration_id":config_id,"configurations":configurations,"resolution_seconds":step,"coverage_seconds":covered_seconds,"coverage_percent":min(100, covered_seconds/(end-start)*100),"statistics":{"peak":peak if covered_seconds else None,"average":weighted/covered_seconds if covered_seconds else None,"viewer_hours":viewer_hours,"covered_seconds":covered_seconds},"live":live_data,"points":points})


def install(app, service):
    viewers = Viewers(service)
    app[web.AppKey("viewers", Viewers)] = viewers
    app.router.add_route("OPTIONS", "/api/v1/viewers/report", viewers.report)
    app.router.add_post("/api/v1/viewers/report", viewers.report)
    app.router.add_get("/api/v1/viewers/live", viewers.live)
    app.router.add_get("/api/v1/viewers/history", viewers.history)
    app.on_startup.append(viewers.start)
    app.on_cleanup.append(viewers.stop)
    app.router.add_get("/api/v1/viewers/config", viewers.config_route)
    app.router.add_put("/api/v1/viewers/config", viewers.config_route)
    app.router.add_post("/api/v1/viewers/key/rotate", viewers.rotate)
