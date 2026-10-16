"""Anonymous presence collection for an Administrator-configured external player."""
import hashlib
import ipaddress
import json
import re
import secrets
import time
from urllib.parse import urlsplit

from aiohttp import web

ID = re.compile(r"^[A-Za-z0-9_-]{16,80}$")


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
        self.db.commit()
        self.rate = {}
        row = self.db.execute("SELECT payload FROM viewer_config WHERE id=1").fetchone()
        self.config = json.loads(row[0]) if row else {"player_url":"", "allowed_origin":"", "public_tracking_url":"", "timezone":"UTC", "timezone_configured":False, "enabled":False, "tracking_hash":"", "last_report_at":None}
        self.config.setdefault("timezone_configured", True)
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
        # A changed player configuration gets a fresh anonymous cohort and key.
        changed = any(updated[k] != self.config[k] for k in ("player_url", "allowed_origin", "public_tracking_url"))
        if changed:
            self.db.execute("DELETE FROM viewer_sessions")
            updated["tracking_hash"] = ""
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
            return web.json_response({"state":"disabled","count":None,"as_of":None,"collector_health":"healthy","last_report_at":self.config["last_report_at"]})
        now = time.time()
        self.db.execute("DELETE FROM viewer_sessions WHERE reported_at<=?", (now - 90,))
        count = self.db.execute("SELECT COUNT(DISTINCT browser_id) FROM viewer_sessions WHERE reported_at>?", (now - 90,)).fetchone()[0]
        return web.json_response({"state":"available","count":count,"as_of":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime(now)),"collector_health":"healthy","last_report_at":self.config["last_report_at"],"expires_after_seconds":90})


def install(app, service):
    viewers = Viewers(service)
    app[web.AppKey("viewers", Viewers)] = viewers
    app.router.add_route("OPTIONS", "/api/v1/viewers/report", viewers.report)
    app.router.add_post("/api/v1/viewers/report", viewers.report)
    app.router.add_get("/api/v1/viewers/live", viewers.live)
    app.router.add_get("/api/v1/viewers/config", viewers.config_route)
    app.router.add_put("/api/v1/viewers/config", viewers.config_route)
    app.router.add_post("/api/v1/viewers/key/rotate", viewers.rotate)
