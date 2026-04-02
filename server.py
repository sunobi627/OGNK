import os, asyncio, httpx, json, hashlib, secrets, shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, UploadFile, File, Form, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from contextlib import asynccontextmanager

# ── Bnovo config ────────────────────────────────────────────────────────────
BNOVO_ID       = os.getenv("BNOVO_ACCOUNT_ID", "")
BNOVO_PASSWORD = os.getenv("BNOVO_PASSWORD", "")
BNOVO_BASE     = "https://api.pms.bnovo.ru/api/v1"

BNOVO_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
}

# ── Cottage room matching (first char ord of Russian room name) ──────────────
BNOVO_CHARS = {
    "1": 1075,  # г — Гармония
    "2": 1085,  # н — Нежность
    "3": 1088,  # р — Радость
    "4": 1073,  # б — Бодрость
    "5": 1089,  # с — Спокойствие
    "6": 1101,  # э — Энергия
}

# ── Settings ─────────────────────────────────────────────────────────────────
SETTINGS_PATH = Path("settings.json")
UPLOADS_DIR   = Path("static/uploads")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_SETTINGS = {
    "property_name":  "Огонёк",
    "greeting_text":  "Добро пожаловать,",
    "support_phone":  "+7 (900) 000-00-00",
    "background_type": "video",
    "background_url":  "https://assets.mixkit.co/videos/11145/11145-720.mp4",
    "background_file": None,
    "cottages": {
        str(i): {"name": f"Домик {i}", "wifi_name": "ogonek", "wifi_pass": "ognk24"}
        for i in range(1, 7)
    },
    "admin_password_hash": hashlib.sha256(b"ogonek").hexdigest(),
}

_settings: dict = {}
_session_token: str | None = None


def load_settings() -> dict:
    global _settings
    if SETTINGS_PATH.exists():
        try:
            _settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            _settings = dict(DEFAULT_SETTINGS)
    else:
        _settings = dict(DEFAULT_SETTINGS)
    return _settings


def save_settings():
    tmp = SETTINGS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(_settings, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(SETTINGS_PATH)


def get_settings() -> dict:
    return _settings


# ── Auth ─────────────────────────────────────────────────────────────────────
def require_admin(x_admin_token: str | None = Header(default=None)):
    if not _session_token or x_admin_token != _session_token:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── Bnovo ────────────────────────────────────────────────────────────────────
jwt_token = ""
current_guests: dict = {i: None for i in range(1, 7)}
device_last_seen: dict = {}  # cottage_id -> datetime


async def get_jwt_token() -> str:
    global jwt_token
    try:
        payload = {"id": int(BNOVO_ID), "password": BNOVO_PASSWORD}
        print(f"[Bnovo] Auth: id={BNOVO_ID!r}")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"{BNOVO_BASE}/auth", json=payload, headers=BNOVO_HEADERS)
            print(f"[Bnovo] Auth response: {r.status_code} {r.text[:300]}")
            if r.status_code != 200:
                return jwt_token
            token = (r.json().get("data", {}) or {}).get("access_token", "")
            if token:
                jwt_token = token
                print("[Bnovo] Token OK")
            return jwt_token
    except Exception as e:
        print(f"[Bnovo] Auth error: {e}")
        return ""


async def fetch_bookings() -> list:
    global jwt_token
    if not jwt_token:
        jwt_token = await get_jwt_token()
    if not jwt_token:
        return []
    today = date.today()
    headers = {**BNOVO_HEADERS, "Authorization": f"Bearer {jwt_token}"}

    async def do_request(client, d_from, d_to):
        params = {"date_from": d_from, "date_to": d_to, "limit": 50, "offset": 0}
        r = await client.get(f"{BNOVO_BASE}/bookings", headers=headers, params=params)
        if r.status_code == 401:
            global jwt_token
            jwt_token = await get_jwt_token()
            if not jwt_token:
                return []
            headers["Authorization"] = f"Bearer {jwt_token}"
            r = await client.get(f"{BNOVO_BASE}/bookings", headers=headers, params=params)
        print(f"[Bnovo] GET bookings {d_from}..{d_to} -> {r.status_code}")
        if r.status_code != 200:
            return []
        return r.json().get("data", {}).get("bookings", [])

    try:
        all_bookings, seen_ids = [], set()
        async with httpx.AsyncClient(timeout=15) as client:
            for offset_days in [0, 16]:
                d_to   = (today - timedelta(days=offset_days) + timedelta(days=1)).isoformat()
                d_from = (today - timedelta(days=offset_days + 15)).isoformat()
                for b in await do_request(client, d_from, d_to):
                    if b["id"] not in seen_ids:
                        seen_ids.add(b["id"])
                        all_bookings.append(b)

        active = []
        for b in all_bookings:
            if (b.get("dates") or {}).get("cancel_date"):
                continue
            arr = ((b.get("dates") or {}).get("real_arrival",   "") or "")
            dep = ((b.get("dates") or {}).get("real_departure", "") or "")
            if not arr or not dep:
                continue
            if date.fromisoformat(arr[:10]) <= today < date.fromisoformat(dep[:10]):
                active.append(b)
        print(f"[Bnovo] fetched={len(all_bookings)}, active={len(active)}")
        return active
    except Exception as e:
        print(f"[Bnovo] error: {e}")
        return []


def find_cottage(booking) -> int | None:
    room = str(booking.get("room_name", "")).lower().strip()
    if not room:
        return None
    first = ord(room[0])
    for cid, ch in BNOVO_CHARS.items():
        if ch == first:
            return int(cid)
    return None


def guest_name(b) -> str:
    c = b.get("customer", {}) or {}
    name = c.get("name", "")
    surname = c.get("surname", "")
    return f"{name} {surname}".strip() or "Гость"


async def sync_bnovo():
    global current_guests
    bookings = await fetch_bookings()
    new = {i: None for i in range(1, 7)}
    for b in bookings:
        cid = find_cottage(b)
        if cid:
            dates = b.get("dates") or {}
            new[cid] = {
                "guest_name": guest_name(b),
                "checkin":    str(dates.get("real_arrival",   "") or "")[:10],
                "checkout":   str(dates.get("real_departure", "") or "")[:10],
            }
    current_guests = new
    print(f"[Bnovo] occupied={sum(1 for v in new.values() if v)}/6")


async def sync_loop():
    await get_jwt_token()
    await sync_bnovo()
    while True:
        await asyncio.sleep(900)
        await sync_bnovo()


# ── App ───────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app):
    load_settings()
    t = asyncio.create_task(sync_loop())
    yield
    t.cancel()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Guest screen ──────────────────────────────────────────────────────────────
@app.get("/screen/{cottage_id}", response_class=HTMLResponse)
async def screen(cottage_id: int):
    return FileResponse("static/guest_screen.html")


@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    return FileResponse("static/admin.html")


# ── Public API ────────────────────────────────────────────────────────────────
@app.get("/api/settings")
async def api_settings_public():
    s = get_settings()
    bg_file = s.get("background_file")
    return {
        "property_name":  s.get("property_name", "Огонёк"),
        "greeting_text":  s.get("greeting_text", "Добро пожаловать,"),
        "support_phone":  s.get("support_phone", ""),
        "background_type": s.get("background_type", "video"),
        "background_src":  f"/static/uploads/{bg_file}" if bg_file else s.get("background_url", ""),
    }


@app.get("/api/cottage/{cottage_id}/current-guest")
async def api_guest(cottage_id: int):
    s    = get_settings()
    cfg  = s["cottages"].get(str(cottage_id), {})
    guest = current_guests.get(cottage_id)
    return {
        "cottage_id":    cottage_id,
        "cottage_name":  cfg.get("name", f"Домик {cottage_id}"),
        "wifi_name":     cfg.get("wifi_name", ""),
        "wifi_pass":     cfg.get("wifi_pass", ""),
        "property_name": s.get("property_name", "Огонёк"),
        "greeting_text": s.get("greeting_text", "Добро пожаловать,"),
        "phone":         s.get("support_phone", ""),
        "has_guest":     guest is not None,
        "guest_name":    guest["guest_name"] if guest else None,
        "checkin":       guest["checkin"]     if guest else None,
        "checkout":      guest["checkout"]    if guest else None,
    }


@app.get("/api/status")
async def api_status():
    s = get_settings()
    return {"updated": datetime.now().isoformat(), "cottages": {
        str(i): {
            "name":     s["cottages"].get(str(i), {}).get("name", f"Домик {i}"),
            "occupied": current_guests[i] is not None,
            "guest":    current_guests[i]["guest_name"] if current_guests[i] else None,
            "checkout": current_guests[i]["checkout"]   if current_guests[i] else None,
        }
        for i in range(1, 7)
    }}


@app.post("/api/sync")
async def api_sync():
    await sync_bnovo()
    return {"ok": True}


@app.post("/api/heartbeat/{cottage_id}")
async def heartbeat(cottage_id: int):
    device_last_seen[cottage_id] = datetime.now()
    return {"ok": True}


@app.get("/api/admin/devices")
async def admin_devices(_=Depends(require_admin)):
    s = get_settings()
    now = datetime.now()
    result = {}
    for i in range(1, 7):
        last = device_last_seen.get(i)
        online = last is not None and (now - last).total_seconds() < 120
        result[str(i)] = {
            "name":        s["cottages"].get(str(i), {}).get("name", f"Домик {i}"),
            "online":      online,
            "last_seen":   last.isoformat() if last else None,
            "seconds_ago": int((now - last).total_seconds()) if last else None,
        }
    return result


# ── Admin API ─────────────────────────────────────────────────────────────────
@app.post("/api/admin/login")
async def admin_login(body: dict):
    global _session_token
    password = body.get("password", "")
    h = hashlib.sha256(password.encode()).hexdigest()
    stored = get_settings().get("admin_password_hash", "")
    if h != stored:
        raise HTTPException(status_code=401, detail="Неверный пароль")
    _session_token = secrets.token_hex(32)
    return {"token": _session_token}


@app.get("/api/admin/settings")
async def admin_get_settings(_=Depends(require_admin)):
    s = dict(get_settings())
    s.pop("admin_password_hash", None)
    return s


@app.post("/api/admin/settings")
async def admin_save_settings(body: dict, _=Depends(require_admin)):
    s = get_settings()
    allowed = {"property_name", "greeting_text", "support_phone",
               "background_type", "background_url", "background_file", "cottages"}
    for k, v in body.items():
        if k in allowed:
            s[k] = v
    save_settings()
    return {"ok": True}


@app.post("/api/admin/change-password")
async def admin_change_password(body: dict, _=Depends(require_admin)):
    current = body.get("current_password", "")
    new_pwd = body.get("new_password", "")
    s = get_settings()
    if hashlib.sha256(current.encode()).hexdigest() != s.get("admin_password_hash", ""):
        raise HTTPException(status_code=400, detail="Неверный текущий пароль")
    if len(new_pwd) < 4:
        raise HTTPException(status_code=400, detail="Пароль слишком короткий")
    s["admin_password_hash"] = hashlib.sha256(new_pwd.encode()).hexdigest()
    save_settings()
    return {"ok": True}


@app.post("/api/admin/upload")
async def admin_upload(file: UploadFile = File(...), _=Depends(require_admin)):
    allowed_types = {"video/mp4", "video/webm", "image/jpeg", "image/png", "image/webp"}
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Недопустимый тип файла")
    filename = Path(file.filename).name
    dest = UPLOADS_DIR / filename
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    s = get_settings()
    s["background_file"] = filename
    s["background_type"] = "video" if file.content_type.startswith("video") else "image"
    save_settings()
    return {"ok": True, "url": f"/static/uploads/{filename}", "filename": filename}


@app.get("/api/admin/uploads")
async def admin_list_uploads(_=Depends(require_admin)):
    files = []
    for f in sorted(UPLOADS_DIR.iterdir()):
        if f.is_file():
            files.append({"name": f.name, "size": f.stat().st_size, "url": f"/static/uploads/{f.name}"})
    return files


@app.delete("/api/admin/uploads/{filename}")
async def admin_delete_upload(filename: str, _=Depends(require_admin)):
    path = UPLOADS_DIR / Path(filename).name
    if path.exists():
        path.unlink()
    s = get_settings()
    if s.get("background_file") == filename:
        s["background_file"] = None
        s["background_type"] = "video"
    save_settings()
    return {"ok": True}
