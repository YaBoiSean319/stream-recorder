"""Anonymous presence collection for an Administrator-configured external player."""
import csv
import hashlib
import io
import ipaddress
import json
import math
import re
import secrets
import time
import asyncio
import uuid
import datetime as dt
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

from aiohttp import web

ID = re.compile(r"^[A-Za-z0-9_-]{16,80}$")
SAMPLE_INTERVAL = 30
DEFAULT_DETAIL_DAYS = 90
DEFAULT_HISTORY_DAYS = 730


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
        self.db.execute("CREATE TABLE IF NOT EXISTS viewer_rollups(hour_start INTEGER NOT NULL,configuration_id TEXT NOT NULL,weighted_sum REAL NOT NULL,covered_seconds REAL NOT NULL,peak INTEGER NOT NULL,PRIMARY KEY(hour_start,configuration_id))")
        self.db.execute("CREATE TABLE IF NOT EXISTS viewer_configurations(configuration_id TEXT PRIMARY KEY,player_url TEXT NOT NULL,created_at REAL NOT NULL)")
        self.db.commit()
        self.rate = {}
        row = self.db.execute("SELECT payload FROM viewer_config WHERE id=1").fetchone()
        self.config = json.loads(row[0]) if row else {"player_url":"", "allowed_origin":"", "public_tracking_url":"", "timezone":"UTC", "timezone_configured":False, "enabled":False, "tracking_hash":"", "last_report_at":None,"detail_retention_days":DEFAULT_DETAIL_DAYS,"history_retention_days":DEFAULT_HISTORY_DAYS}
        self.config.setdefault("timezone_configured", True)
        self.config.setdefault("configuration_id", str(uuid.uuid4()))
        self.config.setdefault("detail_retention_days", DEFAULT_DETAIL_DAYS)
        self.config.setdefault("history_retention_days", DEFAULT_HISTORY_DAYS)
        self._register_configuration(time.time())
        # Preserve selectors for configurations written by the pre-registry
        # history implementation during upgrades.
        historical = self.db.execute("SELECT configuration_id,MIN(sampled_at) FROM viewer_samples GROUP BY configuration_id UNION ALL SELECT configuration_id,MIN(hour_start) FROM viewer_rollups GROUP BY configuration_id").fetchall()
        with self.db:
            for identity, first_at in historical:
                self.db.execute("INSERT OR IGNORE INTO viewer_configurations VALUES(?,?,?)", (identity, self.config["player_url"] if identity == self.config["configuration_id"] else "", first_at))
        # Persisted history cannot prove that this process's collector is healthy.
        self.last_sample_at = None
        self.key_for_response = ""
        self._save()

    def _save(self):
        safe = {k:v for k,v in self.config.items() if k != "tracking_key_once"}
        self.db.execute("INSERT OR REPLACE INTO viewer_config VALUES(1,?)", (json.dumps(safe),))
        self.db.commit()

    def _register_configuration(self, now):
        self.db.execute("INSERT OR IGNORE INTO viewer_configurations VALUES(?,?,?)", (self.config["configuration_id"],self.config["player_url"],now))
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
        return {"player_url":c["player_url"], "allowed_origin":c["allowed_origin"], "public_tracking_url":c["public_tracking_url"], "timezone":c["timezone"], "timezone_configured":c["timezone_configured"], "enabled":c["enabled"], "configuration_id":c["configuration_id"], "detail_retention_days":c["detail_retention_days"], "history_retention_days":c["history_retention_days"], "tracking_key_configured":bool(c["tracking_hash"]), "collector_health":self.collector_health(), "last_report_at":c["last_report_at"], "snippet":self._snippet(key), **({"tracking_key":key} if expose_key and key else {})}

    async def config_route(self, request):
        if request.method == "GET":
            return web.json_response(self._visible())
        body = await request.json()
        if not isinstance(body, dict) or set(body) - {"player_url","allowed_origin","public_tracking_url","timezone","enabled","detail_retention_days","history_retention_days"}:
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
        detail, total = updated["detail_retention_days"], updated["history_retention_days"]
        if isinstance(detail,bool) or not isinstance(detail,int) or isinstance(total,bool) or not isinstance(total,int) or not (1 <= detail <= total <= 730):
            raise web.HTTPBadRequest(text="Retention must use whole days with 1 <= detail <= total <= 730")
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
        if changed:
            self._register_configuration(time.time())
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

    async def revoke(self, request):
        self.config["tracking_hash"] = ""
        self.db.execute("DELETE FROM viewer_sessions")
        self._save()
        return web.json_response({"revoked":True,"tracking_key_configured":False}, headers={"Cache-Control":"no-store"})

    async def configurations(self, request):
        rows = self.db.execute("""SELECT c.configuration_id,c.player_url,c.created_at,
          MIN(COALESCE(s.sampled_at,r.hour_start)),MAX(COALESCE(s.sampled_at,r.hour_start))
          FROM viewer_configurations c
          LEFT JOIN (SELECT configuration_id,sampled_at FROM viewer_samples UNION ALL SELECT configuration_id,hour_start FROM viewer_rollups) x ON x.configuration_id=c.configuration_id
          LEFT JOIN viewer_samples s ON s.configuration_id=x.configuration_id AND s.sampled_at=x.sampled_at
          LEFT JOIN viewer_rollups r ON r.configuration_id=x.configuration_id AND r.hour_start=x.sampled_at
          GROUP BY c.configuration_id ORDER BY c.created_at DESC""").fetchall()
        return web.json_response({"current_configuration_id":self.config["configuration_id"],"items":[{"id":r[0],"player_url":r[1],"created_at":r[2],"first_at":r[3],"last_at":r[4]} for r in rows]})

    async def diagnostics(self, request):
        live = json.loads((await self.live(request)).body)
        return web.json_response({"enabled":self.config["enabled"],"configuration_id":self.config["configuration_id"],"tracking_key_configured":bool(self.config["tracking_hash"]),"collector_health":self.collector_health(),"last_sample_at":self.last_sample_at,"last_report_at":self.config["last_report_at"],"active_browser_count":live.get("count"),"detail_retention_days":self.config["detail_retention_days"],"history_retention_days":self.config["history_retention_days"]}, headers={"Cache-Control":"no-store"})

    async def export_csv(self, request):
        response = await self.history(request)
        data = json.loads(response.body)
        if response.status != 200:
            return response
        out = io.StringIO(newline="")
        writer = csv.writer(out, lineterminator="\r\n")
        def safe(value):
            if value is None:
                return ""
            value = str(value)
            leading = value.lstrip(" \t\r\n")
            return "'" + value if leading and leading[:1] in "=+-@" else value
        def row(*values):
            writer.writerow([safe(value) for value in values])
        row("row_type","name","timestamp_unix_seconds","timezone","configuration_id","resolution_seconds","unit","aggregation","count","average","peak","covered_seconds","expected_seconds","coverage_percent","details")
        row("meta","timezone",data["timezone"])
        row("meta","unit","browsers")
        row("meta","aggregation","time-weighted concurrent browser estimates; unknown intervals excluded")
        row("meta","from_unix_seconds",data["from"])
        row("meta","to_exclusive_unix_seconds",data["to"])
        row("meta","configuration_id",data["configuration_id"])
        row("meta","requested_resolution",request.query.get("resolution","auto"))
        row("meta","resolution_seconds",data["resolution_seconds"])
        row("meta","precision",data["precision"])
        row("meta","detail_retention_days",data["detail_retention_days"])
        row("meta","history_retention_days",data["history_retention_days"])
        row("meta","detail_expired",data["detail_expired"])
        row("meta","history_expired",data["history_expired"])
        row("meta","partial_rollup_hours_excluded",data["partial_rollup_hours_excluded"])
        live=data["live"]
        row("live_status","current",live.get("as_of"),data["timezone"],data["configuration_id"],data["resolution_seconds"],"browsers","current concurrent count",live.get("count"),"","", "", "", "",f"state={live.get('state')};collector_health={live.get('collector_health')};last_report_at={live.get('last_report_at')}")
        stats=data["statistics"]
        row("statistic","peak",data["to"],data["timezone"],data["configuration_id"],data["resolution_seconds"],"browsers","maximum over covered samples", "","",stats["peak"],stats["covered_seconds"],data["to"]-data["from"],data["coverage_percent"])
        row("statistic","average",data["to"],data["timezone"],data["configuration_id"],data["resolution_seconds"],"browsers","time-weighted mean over covered time", "",stats["average"],"",stats["covered_seconds"],data["to"]-data["from"],data["coverage_percent"])
        row("statistic","viewer_hours",data["to"],data["timezone"],data["configuration_id"],data["resolution_seconds"],"browser-hours","sum(count × covered seconds) / 3600",stats["viewer_hours"],"","",stats["covered_seconds"],data["to"]-data["from"],data["coverage_percent"])
        for point in data["points"]:
            row("point","timeline",point["timestamp"],data["timezone"],data["configuration_id"],data["resolution_seconds"],"browsers","bucket average and peak", "",point["average"],point["peak"],point["covered_seconds"],point["expected_seconds"],(point["covered_seconds"]/point["expected_seconds"]*100 if point["expected_seconds"] else 0),"complete="+str(point["complete"]))
        for day in data["daily_peaks"]:
            row("daily_peak",day["date"],day["peak_at"],data["timezone"],data["configuration_id"],data["resolution_seconds"],"browsers","local-date maximum", "", "",day["peak"],day["covered_seconds"],day["expected_seconds"],day["coverage_percent"])
        for cell in data["heatmap"]:
            row("heatmap",f"day={cell['day_of_week']};hour={cell['hour']}","",data["timezone"],data["configuration_id"],data["resolution_seconds"],"browsers","day-of-week/local-hour maximum", "",cell["average"],cell["peak"],cell["covered_seconds"],cell["expected_seconds"],cell["coverage_percent"],"periods="+str(cell["period_count"]))
        for period in data["heatmap"]:
            for item in period["periods"]:
                row("heatmap_period",f"day={period['day_of_week']};hour={period['hour']}",item["from"],data["timezone"],data["configuration_id"],data["resolution_seconds"],"browsers","actual local date/hour/fold", "",item["average"],item["peak"],item["covered_seconds"],item["expected_seconds"],item["coverage_percent"],f"date={item['date']};utc_offset_seconds={item['utc_offset_seconds']};to_exclusive={item['to']}")
        for recording in data["recordings"]:
            row("recording",recording["title"],recording["start"],data["timezone"],data["configuration_id"],data["resolution_seconds"],"seconds","capture context only", "","","","","","",f"end={recording['end']};state={recording['state']};interruptions={recording['interruption_count']}")
        return web.Response(text=out.getvalue(),content_type="text/csv",charset="utf-8",headers={"Content-Disposition":"attachment; filename=\"viewer-analytics.csv\"","Cache-Control":"no-store"})

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
            try:
                body = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise web.HTTPBadRequest(text="Expected a valid JSON report") from None
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
        self._apply_retention(now)

    def _apply_retention(self, now):
        """Compact expired detail into idempotent hourly weighted summaries."""
        detail_cutoff = now - self.config["detail_retention_days"] * 86400
        history_cutoff = now - self.config["history_retention_days"] * 86400
        rows = self.db.execute("SELECT sampled_at,count,configuration_id,covered FROM viewer_samples WHERE sampled_at < ? ORDER BY sampled_at", (detail_cutoff,)).fetchall()
        with self.db:
            for stamp,count,identity,covered in rows:
                if stamp >= history_cutoff and covered and count is not None:
                    next_row = self.db.execute("SELECT sampled_at FROM viewer_samples WHERE configuration_id=? AND sampled_at>? ORDER BY sampled_at LIMIT 1",(identity,stamp)).fetchone()
                    cadence = SAMPLE_INTERVAL
                    duration = min(cadence, next_row[0]-stamp) if next_row and next_row[0]-stamp <= cadence*1.5 else cadence
                    start, end = max(stamp,history_cutoff), min(stamp+duration,detail_cutoff)
                    while start < end:
                        hour = int(start // 3600 * 3600)
                        segment = min(end,hour+3600)-start
                        self.db.execute("""INSERT INTO viewer_rollups VALUES(?,?,?,?,?)
                          ON CONFLICT(hour_start,configuration_id) DO UPDATE SET
                          weighted_sum=weighted_sum+excluded.weighted_sum,
                          covered_seconds=covered_seconds+excluded.covered_seconds,
                          peak=MAX(peak,excluded.peak)""",(hour,identity,count*segment,segment,count))
                        start += segment
            self.db.execute("DELETE FROM viewer_samples WHERE sampled_at < ?",(detail_cutoff,))
            self.db.execute("DELETE FROM viewer_rollups WHERE hour_start + 3600 <= ?",(history_cutoff,))

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
        if not (0 < start < end <= now + 60) or end - start > self.config["history_retention_days"] * 86400:
            raise web.HTTPBadRequest(text="Range must be increasing and no longer than configured history retention")
        return start, end

    async def history(self, request):
        start, end = self.parse_range(request)
        now = time.time()
        try:
            resolution = request.query.get("resolution", "auto")
            if resolution not in {"auto", "30s", "1m", "5m", "15m", "1h", "6h", "1d"}:
                raise ValueError
            config_id = request.query.get("configuration_id", self.config["configuration_id"])
            report_timezone = request.query.get("timezone", self.config["timezone"])
            ZoneInfo(report_timezone)
            if config_id is not None and (len(config_id) > 64 or not re.fullmatch(r"[a-fA-F0-9-]+", config_id)):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise web.HTTPBadRequest(text="Invalid resolution or configuration_id") from None
        step = {"30s":30,"1m":60,"5m":300,"15m":900,"1h":3600,"6h":21600,"1d":86400}.get(resolution)
        if step is None:
            step = max(SAMPLE_INTERVAL, (end-start) / 500)
        rollup_rows = self.db.execute("SELECT hour_start,weighted_sum,covered_seconds,peak FROM viewer_rollups WHERE configuration_id=? AND hour_start < ? AND hour_start+3600 > ? ORDER BY hour_start",(config_id,end,start)).fetchall()
        has_rollup_range = bool(rollup_rows)
        partial_rollup_hours_excluded = sum(1 for hour, *_ in rollup_rows if hour < start or hour + 3600 > end)
        step = max(3600 if has_rollup_range else 1, math.ceil(max(step, (end-start) / 1000)))
        rows = self.db.execute("SELECT sampled_at,count,configuration_id,covered FROM viewer_samples WHERE sampled_at<? AND sampled_at>=? AND configuration_id=? ORDER BY sampled_at", (end, start - SAMPLE_INTERVAL, config_id)).fetchall()
        buckets = {}
        covered_seconds = 0.0
        weighted = 0.0
        viewer_hours = 0.0
        peak = 0
        zone = ZoneInfo(report_timezone)
        daily = {}
        heat = {}
        # Build the expected local calendar periods from the absolute range first.
        # This makes a missing sample visible as an uncovered period and naturally
        # creates a 23-hour spring day or two distinct fall-back folds.
        cursor = start
        while cursor < end:
            local = dt.datetime.fromtimestamp(cursor, zone)
            hour_start = int(local.replace(minute=0, second=0, microsecond=0).timestamp())
            hour_end = hour_start + 3600
            segment_end = min(end, hour_end)
            duration = max(0.0, segment_end - cursor)
            day_key = local.date().isoformat()
            day = daily.setdefault(day_key, {"date":day_key,"peak":None,"peak_at":None,"covered_seconds":0.0,"expected_seconds":0.0})
            day["expected_seconds"] += duration
            key = (local.weekday(), local.hour)
            cell = heat.setdefault(key, {"day_of_week":(local.weekday()+1)%7,"hour":local.hour,"_weighted":0.0,"peak":None,"covered_seconds":0.0,"expected_seconds":0.0,"periods":{}})
            cell["expected_seconds"] += duration
            period_key = (day_key, local.hour, int(local.utcoffset().total_seconds()))
            period = cell["periods"].setdefault(period_key, {"date":day_key,"hour":local.hour,"utc_offset_seconds":int(local.utcoffset().total_seconds()),"from":max(start,hour_start),"to":min(end,hour_end),"_weighted":0.0,"peak":None,"covered_seconds":0.0,"expected_seconds":0.0})
            period["expected_seconds"] += duration
            cursor = segment_end
        def add_activity(stamp, count, duration):
            cursor = stamp
            stop = stamp + duration
            while cursor < stop:
                local = dt.datetime.fromtimestamp(cursor, zone)
                hour_start = int(local.replace(minute=0, second=0, microsecond=0).timestamp())
                segment_end = min(stop, hour_start + 3600)
                segment = segment_end - cursor
                day_key = local.date().isoformat()
                day = daily.setdefault(day_key, {"date":day_key,"peak":None,"peak_at":None,"covered_seconds":0.0,"expected_seconds":0.0})
                day["covered_seconds"] += segment
                if day["peak_at"] is None or count > day["peak"]:
                    day["peak"], day["peak_at"] = count, cursor
                key = (local.weekday(), local.hour)
                cell = heat.setdefault(key, {"day_of_week":(local.weekday()+1)%7,"hour":local.hour,"_weighted":0.0,"peak":None,"covered_seconds":0.0,"expected_seconds":0.0,"periods":{}})
                cell["_weighted"] += count * segment
                if cell["peak"] is None or count > cell["peak"]:
                    cell["peak"] = count
                cell["covered_seconds"] += segment
                offset = int(local.utcoffset().total_seconds())
                period_key = (day_key, local.hour, offset)
                period = cell["periods"].setdefault(period_key, {"date":day_key,"hour":local.hour,"utc_offset_seconds":offset,"from":max(start,hour_start),"to":min(end,hour_start+3600),"_weighted":0.0,"peak":None,"covered_seconds":0.0,"expected_seconds":0.0})
                period["_weighted"] += count * segment
                if period["peak"] is None or count > period["peak"]:
                    period["peak"] = count
                period["covered_seconds"] += segment
                cursor = segment_end
        for i, row in enumerate(rows):
            stamp, count, identity, covered = row
            if not covered:
                continue
            next_stamp = rows[i+1][0] if i+1 < len(rows) and rows[i+1][2] == identity else stamp + SAMPLE_INTERVAL
            if i+1 == len(rows) and end > now - SAMPLE_INTERVAL * 1.5 and now - stamp > SAMPLE_INTERVAL * 1.5:
                continue
            duration = min(end, stamp + SAMPLE_INTERVAL, next_stamp) - max(start, stamp)
            if duration <= 0:
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
            add_activity(stamp, count, duration)
        for hour, weighted_sum, duration, rollup_peak in rollup_rows:
            # Rollups lack sub-hour detail. A requested partial hour remains
            # unknown; complete UTC hours can contribute exact aggregate stats.
            if hour < start or hour + 3600 > end or duration <= 0:
                continue
            bucket = int(hour // step * step)
            item = buckets.setdefault(bucket, {"timestamp":bucket,"average":0,"peak":0,"covered_seconds":0,"_weighted":0})
            item["_weighted"] += weighted_sum
            item["covered_seconds"] += duration
            item["peak"] = max(item["peak"],rollup_peak)
            covered_seconds += duration
            weighted += weighted_sum
            viewer_hours += weighted_sum / 3600
            peak = max(peak,rollup_peak)
            # Old summaries can be assigned to local calendar groups only if
            # the entire UTC hour maps to one local date/hour/fold. Otherwise
            # omit it from those derived views rather than inventing a split.
            local_start = dt.datetime.fromtimestamp(hour,zone)
            local_end = dt.datetime.fromtimestamp(hour+3599,zone)
            if (local_start.date(),local_start.hour,local_start.utcoffset()) != (local_end.date(),local_end.hour,local_end.utcoffset()):
                continue
            day = daily.setdefault(local_start.date().isoformat(), {"date":local_start.date().isoformat(),"peak":None,"peak_at":None,"covered_seconds":0.0,"expected_seconds":0.0})
            day["covered_seconds"] += duration
            if day["peak"] is None or rollup_peak > day["peak"]:
                day["peak"],day["peak_at"] = rollup_peak,None
            key=(local_start.weekday(),local_start.hour)
            cell=heat.setdefault(key,{"day_of_week":(local_start.weekday()+1)%7,"hour":local_start.hour,"_weighted":0.0,"peak":None,"covered_seconds":0.0,"expected_seconds":0.0,"periods":{}})
            cell["_weighted"] += weighted_sum
            cell["covered_seconds"] += duration
            if cell["peak"] is None or rollup_peak > cell["peak"]:
                cell["peak"] = rollup_peak
            offset=int(local_start.utcoffset().total_seconds())
            day_key=local_start.date().isoformat()
            period_key=(day_key,local_start.hour,offset)
            period=cell["periods"].setdefault(period_key,{"date":day_key,"hour":local_start.hour,"utc_offset_seconds":offset,"from":max(start,hour),"to":min(end,hour+3600),"_weighted":0.0,"peak":None,"covered_seconds":0.0,"expected_seconds":0.0})
            period["_weighted"] += weighted_sum
            period["covered_seconds"] += duration
            if period["peak"] is None or rollup_peak > period["peak"]:
                period["peak"] = rollup_peak

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
        configurations = [{"id":r[0],"player_url":r[1],"created_at":r[2],"first_at":r[3],"last_at":r[4]} for r in self.db.execute("""SELECT c.configuration_id,c.player_url,c.created_at,
          MIN(COALESCE(s.sampled_at,r.hour_start)),MAX(COALESCE(s.sampled_at,r.hour_start))
          FROM viewer_configurations c
          LEFT JOIN (SELECT configuration_id,sampled_at FROM viewer_samples UNION ALL SELECT configuration_id,hour_start FROM viewer_rollups) x ON x.configuration_id=c.configuration_id
          LEFT JOIN viewer_samples s ON s.configuration_id=x.configuration_id AND s.sampled_at=x.sampled_at
          LEFT JOIN viewer_rollups r ON r.configuration_id=x.configuration_id AND r.hour_start=x.sampled_at
          GROUP BY c.configuration_id ORDER BY c.created_at DESC""")]
        daily_peaks = []
        for day in daily.values():
            expected = day["expected_seconds"]
            daily_peaks.append({**day,"peak_at":day["peak_at"],"expected_seconds":expected,"coverage_percent":min(100.0, day["covered_seconds"] / expected * 100) if expected else 0.0,"unit":"browsers"})
        heatmap = []
        for cell in heat.values():
            cell["average"] = cell["_weighted"] / cell["covered_seconds"] if cell["covered_seconds"] else None
            periods = []
            for period in cell["periods"].values():
                period["average"] = period["_weighted"] / period["covered_seconds"] if period["covered_seconds"] else None
                period["coverage_percent"] = min(100.0, period["covered_seconds"] / period["expected_seconds"] * 100) if period["expected_seconds"] else 0.0
                del period["_weighted"]
                periods.append(period)
            cell["periods"] = sorted(periods, key=lambda p:(p["from"],p["utc_offset_seconds"]))
            cell["period_count"] = len(periods)
            cell["coverage_percent"] = min(100.0, cell["covered_seconds"] / cell["expected_seconds"] * 100) if cell["expected_seconds"] else 0.0
            cell["unit"] = "browsers"
            del cell["_weighted"]
            heatmap.append(cell)
        recordings = []
        for record in self.service.records.values():
            try:
                rec_start = dt.datetime.fromisoformat(record["started_at"]).timestamp()
            except (KeyError, TypeError, ValueError):
                continue
            try:
                rec_end = dt.datetime.fromisoformat(record["ended_at"]).timestamp() if record.get("ended_at") else (min(now, end) if record.get("state") in {"recording","reconnecting","recovering","processing"} else None)
            except (TypeError, ValueError):
                rec_end = rec_start
            if (rec_end is None and rec_start <= end and record.get("state") in {"recording","reconnecting","recovering","processing"}) or (rec_end is not None and rec_end >= start and rec_start <= end):
                recordings.append({"id":record["id"],"title":record.get("title") or record.get("filename") or "Recording","state":record.get("state"),"start":rec_start,"end":min(rec_end, now, end) if rec_end is not None and record.get("state") in {"recording","reconnecting","recovering","processing"} else rec_end,"interruption_count":len(record.get("interruptions",[]))})
        recordings.sort(key=lambda r:r["start"])
        detail_expired = start < now - self.config["detail_retention_days"] * 86400
        history_expired = start < now - self.config["history_retention_days"] * 86400
        return web.json_response({"from":start,"to":end,"timezone":report_timezone,"configuration_id":config_id,"configurations":configurations,"resolution_seconds":step,"precision":"hourly-rollup" if has_rollup_range else "sampled","partial_rollup_hours_excluded":partial_rollup_hours_excluded,"detail_expired":detail_expired,"history_expired":history_expired,"detail_retention_days":self.config["detail_retention_days"],"history_retention_days":self.config["history_retention_days"],"coverage_seconds":covered_seconds,"coverage_percent":min(100, covered_seconds/(end-start)*100),"statistics":{"peak":peak if covered_seconds else None,"average":weighted/covered_seconds if covered_seconds else None,"viewer_hours":viewer_hours,"covered_seconds":covered_seconds},"live":live_data,"points":points,"daily_peaks":sorted(daily_peaks,key=lambda d:d["date"]),"heatmap":sorted(heatmap,key=lambda c:(c["day_of_week"],c["hour"])),"recordings":recordings})


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
    app.router.add_delete("/api/v1/viewers/key", viewers.revoke)
    app.router.add_get("/api/v1/viewers/configurations", viewers.configurations)
    app.router.add_get("/api/v1/viewers/diagnostics", viewers.diagnostics)
    app.router.add_get("/api/v1/viewers/export.csv", viewers.export_csv)
