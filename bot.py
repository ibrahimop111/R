#!/usr/bin/env python3
"""Badge rig — public bot, user-token payload, zero config, no cf_clearance prompt."""
import asyncio, base64, hashlib, json, os, random, sqlite3, struct, time, uuid
import aiohttp, aiohttp.web as web
from curl_cffi import requests as creq
import discord
from discord import app_commands
from cryptography.fernet import Fernet

APP_ID = 1571928909556228157
TOKEN  = os.environ.get("BOT_TOKEN", "MTU3MTkyODkwOTU1NjIyODE1Nw.Giiiei.Hm_wtLfGTSGrnFmE5G1QW6KlBT9kotj7TMgr7o")
PORT   = int(os.environ.get("PORT", 8080))
SELF   = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("SELF_URL")

HOSTS = ("https://discord.com/api/v9", "https://discordapp.com/api/v9")
CF    = os.environ.get("CF_CLEARANCE")          # silent fallback only
MAX_PAR = int(os.environ.get("MAX_PARALLEL", "2"))
COOLDOWN_S = 600
HOUSES = {"bravery": 1, "brilliance": 2, "balance": 3}
LABELS = {"bravery": "Bravery", "brilliance": "Brilliance", "balance": "Balance", "remove": "none"}

# deterministic key -> survives every restart, no env var to set
FER = Fernet(base64.urlsafe_b64encode(hashlib.sha256(("badge:" + TOKEN).encode()).digest()))
SEM = asyncio.Semaphore(MAX_PAR)
COOL: dict[int, float] = {}

# ------------------------------------------------------------------ storage
db = sqlite3.connect("tokens.db", check_same_thread=False)
db.execute("CREATE TABLE IF NOT EXISTS u (id TEXT PRIMARY KEY, tok BLOB)")
db.commit()
DB_LOCK = asyncio.Lock()

async def save_token(uid: int, tok: str):
    async with DB_LOCK:
        db.execute("INSERT OR REPLACE INTO u VALUES (?,?)", (str(uid), FER.encrypt(tok.encode())))
        db.commit()

async def load_token(uid: int):
    async with DB_LOCK:
        r = db.execute("SELECT tok FROM u WHERE id=?", (str(uid),)).fetchone()
    return FER.decrypt(r[0]).decode() if r else None

# ------------------------------------------------------------------ http
SUPER_PROPS = base64.b64encode(json.dumps({
    "os": "Windows", "browser": "Chrome", "device": "", "system_locale": "en-US",
    "browser_version": "138.0.0.0", "os_version": "10.0", "referrer": "",
    "referring_domain": "", "referrer_current": "", "referring_domain_current": "",
    "release_channel": "stable", "client_build_number": 391000,
    "client_event_source": None,
}, separators=(",", ":")).encode()).decode()

def _headers(tok):
    h = {
        "authorization": tok,
        "origin": "https://discord.com",
        "referer": "https://discord.com/channels/@me",
        "accept-language": "en-US,en;q=0.9",
        "x-super-properties": SUPER_PROPS,
        "x-discord-locale": "en-US",
    }
    if CF:
        h["cookie"] = f"cf_clearance={CF}"
    return h

def _req(method: str, path: str, tok: str, body=None):
    """Host fallback + CF retry. UA left to curl_cffi so it matches the handshake."""
    last = (0, None)
    with creq.Session(impersonate="chrome") as s:
        for host in HOSTS:
            for attempt in range(2):
                try:
                    r = s.request(method, host + path, headers=_headers(tok),
                                  json=body, timeout=30)
                except Exception:
                    last = (0, None)
                    continue
                last = (r.status_code, r)
                if r.status_code == 429:
                    time.sleep(float(r.headers.get("retry-after", 4)))
                    continue
                if r.status_code != 403:
                    break
                time.sleep(1.2)
            if last[0] not in (0, 403):
                break
    r = last[1]
    if r is None:
        return 0, None
    try:
        return r.status_code, (r.json() if r.content else None)
    except Exception:
        return r.status_code, None

async def req(*a):
    return await asyncio.to_thread(_req, *a)

# ------------------------------------------------------------------ payload
def _fingerprint(uid: str) -> str:
    buf = bytearray(146)
    buf[0], buf[1] = 1, 1
    struct.pack_into("<Q", buf, 2, 65432100)
    struct.pack_into("<I", buf, 10, 1690000000)
    struct.pack_into("<I", buf, 14, 0x02500000)
    sha = lambda b: hashlib.sha256(b).digest()
    u = uid.encode()
    buf[18:50]   = sha(b"pe_header_" + u)
    buf[50:82]   = sha(str(uuid.uuid4()).encode() + u)
    buf[82:114]  = sha(b"pe_section_" + u)
    buf[114:146] = sha(b"pe_cert_" + u)
    k = sha(u + b"https://discord.com/careers")
    for i in range(1, 146):
        buf[i] ^= k[(i - 1) & 31]
    return base64.b64encode(bytes(buf)).decode()

def _triplet(game, hours, fp, sess):
    now = int(time.time() * 1000)
    dur = int(max(0.1, hours) * 3_600_000)
    sid, start = str(uuid.uuid4()), now - dur

    def n():
        sess["seq"] += 1
        return sess["seq"]

    def beat(d, init, final, ts):
        return {"type": "running_game_heartbeat", "properties": {
            "client_track_timestamp": ts, "client_heartbeat_session_id": sess["hid"],
            "event_sequence_number": n(), "game_id": game["id"],
            "game_name": game["name"], "game_metadata": None,
            "game_executable": game["exe"], "game_detection_enabled": True,
            "initial_heartbeat": init, "final_heartbeat": final,
            "game_session_id": sid, "duration_tracked_ms": d,
            "rtc_connection_id": None, "media_session_id": None,
            "launch_signature": sess["sig"], "client_app_state": "focused",
            "client_send_timestamp": ts}}

    launch = {"type": "launch_game", "properties": {
        "client_track_timestamp": now, "client_heartbeat_session_id": sess["hid"],
        "event_sequence_number": n(), "game": game["name"], "game_id": game["id"],
        "verified": True, "elevated": False, "is_launcher": False,
        "game_platform": "desktop", "detection_method": "verified_game",
        "is_overlay_enabled": False, "is_overlay_game_enabled": True,
        "is_overlay_game_source": "OOP_DEFAULT_DATABASE",
        "fullscreen_type": "UNKNOWN", "hardware_display_count": 1,
        "overlay_method": "Disabled", "activity_status_enabled": True,
        "activity_status_shared_guilds": [], "current_user_status": "online",
        "game_detection_enabled": True, "executable_path": game["exe"],
        "voice_channel_id": None, "voice_channel_type": None,
        "voice_channel_bitrate": None, "voice_channel_guild_id": None,
        "hidden_by_distributor": False, "game_metadata": None,
        "executable_fingerprint": fp, "client_performance_cpu": None,
        "client_performance_memory": None, "cpu_core_count": None,
        "accessibility_features": 0, "rendered_locale": "en-US",
        "launch_signature": sess["sig"], "client_rtc_state": None,
        "client_app_state": "focused", "client_send_timestamp": now}}
    return [beat(0, True, False, start), launch, beat(dur, False, True, now)]

# ------------------------------------------------------------------ bot
client = discord.Client(intents=discord.Intents.default())
tree = app_commands.CommandTree(client)

async def need_token(inter: discord.Interaction):
    tok = await load_token(inter.user.id)
    if not tok:
        await inter.followup.send("Run `/link` first — your token isn't stored.", ephemeral=True)
    return tok

@tree.command(name="ping", description="Health check")
async def ping(inter):
    await inter.response.send_message("alive", ephemeral=True)

@tree.command(name="link", description="Store your account token (encrypted)")
@app_commands.describe(token="Your account token — not the bot token")
async def link(inter, token: str):
    await inter.response.defer(ephemeral=True)
    tok = token.strip().strip('"')
    st, me = await req("GET", "/users/@me", tok)
    if st != 200:
        return await inter.followup.send(
            f"Rejected (`HTTP {st}`). Get a fresh one: Discord in **a browser** → DevTools → "
            "Network → any request → Request Headers → `authorization`.", ephemeral=True)
    await save_token(inter.user.id, tok)
    await inter.followup.send(f"Linked **{me['username']}**.", ephemeral=True)

@tree.command(name="unlink", description="Delete your stored token")
async def unlink(inter):
    await inter.response.defer(ephemeral=True)
    async with DB_LOCK:
        db.execute("DELETE FROM u WHERE id=?", (str(inter.user.id),))
        db.commit()
    await inter.followup.send("Deleted.", ephemeral=True)

@tree.command(name="profile", description="Show badges on the linked account")
async def profile(inter):
    await inter.response.defer(ephemeral=True)
    tok = await need_token(inter)
    if not tok:
        return
    st, me = await req("GET", "/users/@me", tok)
    if st != 200:
        return await inter.followup.send(f"`HTTP {st}` — re-run `/link`.", ephemeral=True)
    st, p = await req("GET", f"/users/{me['id']}/profile?with_mutual_guilds=false", tok)
    if st != 200:
        return await inter.followup.send(f"Profile read failed (`HTTP {st}`).", ephemeral=True)
    got = [b.get("description", "?") for b in (p or {}).get("badges") or []]
    body = "\n".join(f"- {b}" for b in got) if got else "- none"
    await inter.followup.send(f"**{me['username']}**\n{body}", ephemeral=True)

@tree.command(name="hypesquad", description="Set or clear a HypeSquad house — instant")
@app_commands.choices(house=[
    app_commands.Choice(name="Bravery", value="bravery"),
    app_commands.Choice(name="Brilliance", value="brilliance"),
    app_commands.Choice(name="Balance", value="balance"),
    app_commands.Choice(name="Remove", value="remove")])
async def hypesquad(inter, house: app_commands.Choice[str]):
    await inter.response.defer(ephemeral=True)
    tok = await need_token(inter)
    if not tok:
        return
    await req("DELETE", "/hypesquad/online", tok)
    if house.value == "remove":
        return await inter.followup.send("Cleared. Hard-refresh Discord.", ephemeral=True)
    await asyncio.sleep(1.2)
    st, _ = await req("POST", "/hypesquad/online", tok, {"house_id": HOUSES[house.value]})
    if st in (200, 204):
        msg = f"Joined **{LABELS[house.value]}**. Hard-refresh Discord."
    elif st == 429:
        msg = "Rate limited — retry in a minute."
    elif st == 500:
        msg = "Discord threw a 500 on the DELETE step (common). Retry shortly."
    else:
        msg = f"`HTTP {st}`"
    await inter.followup.send(msg, ephemeral=True)

@tree.command(name="claim", description="Forge Game Time + Game Variety")
@app_commands.describe(games="Unique titles (100 = max tier)", hours="Hours per title")
async def claim(inter, games: app_commands.Range[int, 1, 300] = 100,
                hours: app_commands.Range[float, 0.1, 200] = 50):
    await inter.response.defer(ephemeral=True)
    tok = await need_token(inter)
    if not tok:
        return
    left = COOLDOWN_S - (time.time() - COOL.get(inter.user.id, 0))
    if left > 0:
        return await inter.followup.send(f"Cooldown — {int(left)}s.", ephemeral=True)

    async with SEM:
        st, me = await req("GET", "/users/@me?with_analytics_token=true", tok)
        atok = (me or {}).get("analytics_token")
        if st != 200 or not atok:
            return await inter.followup.send(f"Auth failed (`HTTP {st}`) — re-run `/link`.",
                                             ephemeral=True)
        uid = me["id"]

        st, cat = await req("GET", "/applications/detectable", tok)
        if st != 200 or not cat:
            return await inter.followup.send(f"Catalog failed (`HTTP {st}`).", ephemeral=True)

        fp, pool, seen = _fingerprint(uid), [], set()
        for g in cat:
            if not g.get("id") or g["id"] in seen:
                continue
            exe = next((e.get("name") for e in g.get("executables") or []
                        if e.get("os") == "win32"), None)
            if not exe:
                continue
            seen.add(g["id"])
            pool.append({"id": str(g["id"]), "name": g["name"],
                         "exe": f"c:/program files/{g['name'].lower()}/{exe}"})
        random.shuffle(pool)
        sel = pool[:min(games, len(pool))]
        if not sel:
            return await inter.followup.send("No usable titles in the catalog.", ephemeral=True)

        sess = {"hid": str(uuid.uuid4()), "sig": str(uuid.uuid4()), "seq": 0}
        ok = 0
        for i in range(0, len(sel), 25):
            chunk = sel[i:i + 25]
            events = [e for g in chunk for e in _triplet(g, hours, fp, sess)]
            st, _ = await req("POST", "/science", tok, {"token": atok, "events": events})
            if st in (200, 204):
                ok += len(chunk)
            elif st in (401, 403):
                return await inter.followup.send(
                    f"`HTTP {st}` on batch {i // 25 + 1} — token stale, or this host's IP is "
                    "challenged. Try again in a few minutes.", ephemeral=True)
            await asyncio.sleep(0.45 + random.uniform(-0.15, 0.15))

    COOL[inter.user.id] = time.time()
    await inter.followup.send(
        f"Submitted **{ok}/{len(sel)} titles**, {ok * hours:,.0f}h total.\n"
        "Badges land on Discord's 24–48h cycle. Re-running **stacks** hours.",
        ephemeral=True)

@tree.error
async def on_err(inter: discord.Interaction, error: app_commands.AppCommandError):
    msg = f"`{type(error).__name__}: {error}`"[:1500]
    try:
        if inter.response.is_done():
            await inter.followup.send(msg, ephemeral=True)
        else:
            await inter.response.send_message(msg, ephemeral=True)
    except Exception:
        pass

_synced = False

@client.event
async def on_ready():
    global _synced
    print(f"up as {client.user}", flush=True)
    if _synced:
        return
    _synced = True
    try:
        await tree.sync()                       # global, up to 1h to propagate
    except Exception as e:
        print(f"global sync: {e}", flush=True)

@client.event
async def on_guild_join(guild):
    try:
        tree.copy_global_to(guild=guild)        # instant in servers joined later
        await tree.sync(guild=guild)
    except Exception as e:
        print(f"guild sync: {e}", flush=True)

# ------------------------------------------------------------------ host
async def _health(_):
    return web.Response(text="ok")

async def _keepalive():
    if not SELF:
        return
    async with aiohttp.ClientSession() as s:
        while True:
            await asyncio.sleep(600)
            try:
                await s.get(SELF, timeout=aiohttp.ClientTimeout(total=10))
            except Exception:
                pass

async def main():
    app = web.Application()
    app.router.add_get("/", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    asyncio.create_task(_keepalive())
    async with client:
        await client.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
