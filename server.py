import os, asyncio, httpx
from datetime import date, datetime, timedelta
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from contextlib import asynccontextmanager

BNOVO_ID       = os.getenv("BNOVO_ACCOUNT_ID", "")
BNOVO_PASSWORD = os.getenv("BNOVO_PASSWORD", "")
BNOVO_BASE     = "https://api.pms.bnovo.ru/api/v1"
PROPERTY_NAME  = os.getenv("PROPERTY_NAME", "Ogonek")
SUPPORT_PHONE  = os.getenv("SUPPORT_PHONE", "+7 (900) 000-00-00")

# bnovo_char = ord() of first char of room name (lowercase), e.g. ord('g')=1075 for Garmonia
# This avoids Cyrillic string literals which get corrupted via clipboard
COTTAGES = {
    1: {"name": os.getenv("COTTAGE_1_NAME", "Dom Garmonii"),    "wifi_name": "ogonek", "wifi_pass": "ognk24", "bnovo_char": 1075},
    2: {"name": os.getenv("COTTAGE_2_NAME", "Dom Nezhnosti"),   "wifi_name": "ogonek", "wifi_pass": "ognk24", "bnovo_char": 1085},
    3: {"name": os.getenv("COTTAGE_3_NAME", "Dom Radosti"),     "wifi_name": "ogonek", "wifi_pass": "ognk24", "bnovo_char": 1088},
    4: {"name": os.getenv("COTTAGE_4_NAME", "Dom Bodrosti"),    "wifi_name": "ogonek", "wifi_pass": "ognk24", "bnovo_char": 1073},
    5: {"name": os.getenv("COTTAGE_5_NAME", "Dom Spokojstvija"),"wifi_name": "ogonek", "wifi_pass": "ognk24", "bnovo_char": 1089},
    6: {"name": os.getenv("COTTAGE_6_NAME", "Dom Energii"),     "wifi_name": "ogonek", "wifi_pass": "ognk24", "bnovo_char": 1101},
}

current_guests = {i: None for i in range(1, 7)}
jwt_token = ""

BNOVO_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
}


async def get_jwt_token() -> str:
    global jwt_token
    try:
        payload = {"id": int(BNOVO_ID), "password": BNOVO_PASSWORD}
        print(f"[Bnovo] Auth: id={BNOVO_ID!r}, url={BNOVO_BASE}/auth")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{BNOVO_BASE}/auth",
                json=payload,
                headers=BNOVO_HEADERS,
            )
            print(f"[Bnovo] Auth response: {r.status_code} {r.text[:500]}")
            if r.status_code != 200:
                return jwt_token
            data = r.json()
            token = (data.get("data", {}) or {}).get("access_token", "")
            if token:
                jwt_token = token
                print("[Bnovo] Token received OK")
            else:
                print(f"[Bnovo] Token not found: {data}")
            return jwt_token
    except Exception as e:
        print(f"[Bnovo] Auth error: {e}")
        return ""


async def fetch_bookings() -> list:
    """Fetch bookings and return only those with an active stay today.

    The Bnovo date_from/date_to filter by booking CREATION date, not arrival.
    The API allows ~16 days per request, so we do two 16-day windows (last 32 days)
    to catch any booking created recently enough to cover the current stay.
    """
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
            print("[Bnovo] Token expired, refreshing...")
            global jwt_token
            jwt_token = await get_jwt_token()
            if not jwt_token:
                return []
            headers["Authorization"] = f"Bearer {jwt_token}"
            r = await client.get(f"{BNOVO_BASE}/bookings", headers=headers, params=params)
        print(f"[Bnovo] GET bookings {d_from}..{d_to} -> {r.status_code}")
        if r.status_code != 200:
            print(f"[Bnovo] Error body: {r.text[:300]}")
            return []
        return r.json().get("data", {}).get("bookings", [])

    try:
        all_bookings = []
        seen_ids = set()
        async with httpx.AsyncClient(timeout=15) as client:
            # Window 1: created in last 16 days (covers bookings made recently)
            d_to   = (today + timedelta(days=1)).isoformat()
            d_from = (today - timedelta(days=15)).isoformat()
            for b in await do_request(client, d_from, d_to):
                if b["id"] not in seen_ids:
                    seen_ids.add(b["id"])
                    all_bookings.append(b)

            # Window 2: created 16-32 days ago (covers long-advance bookings for current stays)
            d_to   = (today - timedelta(days=15)).isoformat()
            d_from = (today - timedelta(days=31)).isoformat()
            for b in await do_request(client, d_from, d_to):
                if b["id"] not in seen_ids:
                    seen_ids.add(b["id"])
                    all_bookings.append(b)

        # Filter: arrival_date <= today < departure_date, not cancelled
        active = []
        for b in all_bookings:
            if (b.get("dates") or {}).get("cancel_date"):
                continue
            arrival_str   = ((b.get("dates") or {}).get("real_arrival",   "") or "")
            departure_str = ((b.get("dates") or {}).get("real_departure", "") or "")
            if not arrival_str or not departure_str:
                continue
            arrival_date   = date.fromisoformat(arrival_str[:10])
            departure_date = date.fromisoformat(departure_str[:10])
            if arrival_date <= today < departure_date:
                active.append(b)

        print(f"[Bnovo] Total fetched: {len(all_bookings)}, active today: {len(active)}")
        return active
    except Exception as e:
        print(f"[Bnovo] Bookings error: {e}")
        return []


def find_cottage(booking):
    room_raw = booking.get("room_name", "")
    room = str(room_raw).lower().strip()
    print(f"[find_cottage] room={repr(room)} first_char_ord={ord(room[0]) if room else None}")
    if not room:
        return None
    first = ord(room[0])
    for cid, cfg in COTTAGES.items():
        if cfg["bnovo_char"] == first:
            print(f"  -> matched cid={cid}")
            return cid
    print(f"  -> no match for ord={first}")
    return None


def guest_name(b) -> str:
    customer = b.get("customer", {}) or {}
    name = customer.get("name", "")
    surname = customer.get("surname", "")
    if name or surname:
        return f"{name} {surname}".strip()
    return "Guest"


def parse_date(dt_str) -> str:
    if not dt_str:
        return ""
    return str(dt_str)[:10]


async def sync_bnovo():
    global current_guests
    bookings = await fetch_bookings()
    print(f"[Bnovo] Got {len(bookings)} bookings")
    new = {i: None for i in range(1, 7)}
    for b in bookings:
        cid = find_cottage(b)
        if cid:
            new[cid] = {
                "guest_name": guest_name(b),
                "checkin":  parse_date((b.get("dates") or {}).get("arrival", "")),
                "checkout": parse_date((b.get("dates") or {}).get("departure", "")),
            }
            print(f"  -> cid={cid} name={COTTAGES[cid]['name']}: {new[cid]['guest_name']}")
    current_guests = new
    print(f"[Bnovo] Occupied: {sum(1 for v in new.values() if v)}/6")


async def sync_loop():
    await get_jwt_token()
    await sync_bnovo()
    while True:
        await asyncio.sleep(900)
        await sync_bnovo()


@asynccontextmanager
async def lifespan(app):
    t = asyncio.create_task(sync_loop())
    yield
    t.cancel()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/screen/{cottage_id}", response_class=HTMLResponse)
async def screen(cottage_id: int):
    return FileResponse("static/guest_screen.html")


@app.get("/api/cottage/{cottage_id}/current-guest")
async def api_guest(cottage_id: int):
    cfg = COTTAGES.get(cottage_id, {})
    guest = current_guests.get(cottage_id)
    return {
        "cottage_id":    cottage_id,
        "cottage_name":  cfg.get("name", ""),
        "wifi_name":     cfg.get("wifi_name", "ogonek"),
        "wifi_pass":     cfg.get("wifi_pass", "ognk24"),
        "property_name": PROPERTY_NAME,
        "phone":         SUPPORT_PHONE,
        "has_guest":     guest is not None,
        "guest_name":    guest["guest_name"] if guest else None,
        "checkin":       guest["checkin"]     if guest else None,
        "checkout":      guest["checkout"]    if guest else None,
    }


@app.get("/api/status")
async def api_status():
    return {"updated": datetime.now().isoformat(), "cottages": {
        str(i): {
            "name":     COTTAGES[i]["name"],
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
