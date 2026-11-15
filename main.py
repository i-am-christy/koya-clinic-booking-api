import os
import re
import uuid
from datetime import datetime
from pathlib import Path
import base64
import hashlib
import json

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, field_validator

from db import get_conn

from starlette.exceptions import HTTPException as StarletteHTTPException
import httpx

app = FastAPI()

@app.get("/")
def health():
    return {"message": "Hello, the clinic booking API is running"}

@app.get("/app", response_class=HTMLResponse)
async def booking_page():
    return (Path(__file__).parent / "index.html").read_text(encoding="utf-8")

@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    first = exc.errors()[0]
    field = ".".join(str(p) for p in first["loc"] if p not in ("body", "query", "path", "header"))
    message = f"{field}: {first['msg']}" if field else first["msg"]
    return JSONResponse(status_code=400, content={"error": message})

@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": str(exc.detail)},
        headers=exc.headers,
    )

PAGE_ALLOWED = {"slots", "bookings"}

@app.api_route("/page-api/{path:path}", methods=["GET", "POST", "DELETE"], include_in_schema=False)
async def page_proxy(path: str, request: Request):
    # The web page calls this route instead of the API directly, so the key never reaches the browser.
    if path.split("/")[0] not in PAGE_ALLOWED:
        raise HTTPException(404, "not found")

    headers = {"X-API-Key": os.environ.get("API_KEY", ""), "Content-Type": "application/json"}
    if request.headers.get("Idempotency-Key"):
        headers["Idempotency-Key"] = request.headers["Idempotency-Key"]

    # Call the app in-process, so no network request leaves the server
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://internal") as client:
        resp = await client.request(
            request.method,
            f"/{path}",
            params=request.query_params,
            content=await request.body(),
            headers=headers,
        )

    out_headers = {}
    if "Retry-After" in resp.headers:
        out_headers["Retry-After"] = resp.headers["Retry-After"]
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type="application/json",
        headers=out_headers,
    )

def check_api_key(x_api_key: str = Header(default=None, alias="X-API-Key")):
    if x_api_key != os.environ.get("API_KEY") or x_api_key is None:
        raise HTTPException(status_code=401, detail="Invalid API Key")

class Slot(BaseModel):
    id: str
    provider: str
    start: datetime
    end: datetime

class ResetRequest(BaseModel):
    slots: list[Slot]

@app.post("/reset", dependencies=[Depends(check_api_key)])
def reset_slots(body: ResetRequest):
    with get_conn() as conn:
        # Delete child tables first so the foreign keys don't block the deletes
        conn.execute("DELETE FROM idempotency_keys")
        conn.execute("DELETE FROM booking_attempts")
        conn.execute("DELETE FROM bookings")
        conn.execute("DELETE FROM slots")
        for slot in body.slots:
            conn.execute(
                "INSERT INTO slots (id, start_time, end_time, provider) VALUES (%s, %s, %s, %s)",
                (slot.id, slot.start, slot.end, slot.provider),
            )
    return {"message": "Done"}

@app.get("/slots", dependencies=[Depends(check_api_key)])
async def get_slots(provider: str | None = None):
    with get_conn() as conn:
        rows = conn.execute("""
        SELECT id, provider, start_time, end_time,
            NOT EXISTS (
                SELECT 1 FROM bookings
                WHERE bookings.slot_id = slots.id
                    AND bookings.status = 'confirmed'
            ) AS available
            FROM slots
            WHERE (%s::text IS NULL OR provider = %s)
        ORDER BY start_time
        """, (provider, provider)).fetchall()

        slots = []

        for slot_id, slot_provider, start, end, available in rows:
            slots.append( {
                "id": str(slot_id),
                "provider": slot_provider,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "available": bool(available)
            })
    return {"slots": slots}


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

class NewBooking(BaseModel):
    slot_id: str
    name: str
    email: str

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("cannot be blank")
        return value

    @field_validator("email")
    @classmethod
    def clean_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL_RE.match(value):
            raise ValueError("is not a valid email address")
        return value

    @field_validator("slot_id")
    @classmethod
    def clean_slot_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("cannot be blank")
        return value


@app.post("/bookings", status_code=201, dependencies=[Depends(check_api_key)])
def create_booking(
    body: NewBooking,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    req_hash = request_hash(body)

    # Level 4: a retry returns the original response. It isn't a new attempt, so it skips the rate limit.
    if idempotency_key:
        stored_hash, existing = find_by_idempotency_key(idempotency_key)
        if existing:
            if stored_hash != req_hash:
                raise HTTPException(422, "Idempotency-Key was already used with a different request")
            return existing

    # Level 5: count real attempts only
    check_rate_limit(body.email)

    booking_id = str(uuid.uuid4())
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT start_time <= NOW() FROM slots WHERE id = %s", (body.slot_id,))
            row = cur.fetchone()
            if row is None:
                raise HTTPException(404, "slot not found")
            if row[0]:
                raise HTTPException(400, "slot has already started")

            cur.execute(
                f"""
                INSERT INTO bookings (id, slot_id, name, email, status, created_at)
                VALUES (%s, %s, %s, %s, 'confirmed', NOW())
                RETURNING {BOOKING_COLS}
                """,
                (booking_id, body.slot_id, body.name, body.email),
            )
            booking = booking_json(cur.fetchone())

            if idempotency_key:
                cur.execute(
                    "INSERT INTO idempotency_keys (key, request_hash, booking_id) VALUES (%s, %s, %s)",
                    (idempotency_key, req_hash, booking_id),
                )
        return booking

    except psycopg.errors.UniqueViolation as e:
        if e.diag.constraint_name == "one_confirmed_per_slot":
            # A retry of the same request can lose the race to its own first attempt.
            # In that case return the booking it already made.
            if idempotency_key:
                stored_hash, existing = find_by_idempotency_key(idempotency_key)
                if existing and stored_hash == req_hash:
                    return existing
            raise HTTPException(409, "slot is already booked")

        if e.diag.constraint_name == "idempotency_keys_pkey":
            # A concurrent request used the same key first
            stored_hash, existing = find_by_idempotency_key(idempotency_key)
            if existing and stored_hash == req_hash:
                return existing
            raise HTTPException(422, "Idempotency-Key was already used with a different request")

        raise

def encode_cursor(created_at: datetime, booking_id: str) -> str:
    raw = f"{created_at.isoformat()}|{booking_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()

def decode_cursor(cursor: str):
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts, booking_id = raw.split("|", 1)
        return datetime.fromisoformat(ts), booking_id
    except Exception:
        raise HTTPException(400, "invalid cursor")

@app.get("/bookings", dependencies=[Depends(check_api_key)])
def list_bookings(
    email: str = Query(...),
    limit: int = Query(10, ge=1, le=50),
    cursor: str | None = Query(None),
):
    email = email.strip().lower()
    params = [email]
    cursor_sql = ""
    if cursor:
        after_time, after_id = decode_cursor(cursor)
        cursor_sql = "AND (created_at, id) > (%s, %s)"
        params += [after_time, after_id]
    params.append(limit + 1)  # fetch one extra row to know whether a next page exists

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {BOOKING_COLS} FROM bookings
            WHERE email = %s {cursor_sql}
            ORDER BY created_at ASC, id ASC
            LIMIT %s
            """,
            params,
        )
        rows = cur.fetchall()

    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1]
        next_cursor = encode_cursor(last[5], last[0])

    return {"bookings": [booking_json(r) for r in page], "next_cursor": next_cursor}

@app.get("/bookings/{booking_id}", dependencies=[Depends(check_api_key)])
def get_booking(booking_id: str):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {BOOKING_COLS} FROM bookings WHERE id = %s", (booking_id,))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(404, "booking not found")
    return booking_json(row)

@app.delete("/bookings/{booking_id}", dependencies=[Depends(check_api_key)])
def cancel_booking(booking_id: str):
    with get_conn() as conn, conn.cursor() as cur:
        # Only a confirmed booking can change. The WHERE clause does the check.
        cur.execute(
            f"""
            UPDATE bookings SET status = 'cancelled'
            WHERE id = %s AND status = 'confirmed'
            RETURNING {BOOKING_COLS}
            """,
            (booking_id,),
        )
        row = cur.fetchone()
        if row:
            return booking_json(row)

        # Nothing changed: either the booking doesn't exist or it was already cancelled
        cur.execute("SELECT status FROM bookings WHERE id = %s", (booking_id,))
        row = cur.fetchone()

    if row is None:
        raise HTTPException(404, "booking not found")
    raise HTTPException(409, "booking is already cancelled")  

#rate limiting
MAX_ATTEMPTS = 5
WINDOW_SECONDS = 60

def check_rate_limit(email: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO booking_attempts (email) VALUES (%s)", (email,))
        cur.execute(
            """
            SELECT COUNT(*) FROM booking_attempts
            WHERE email = %s AND attempted_at > NOW() - make_interval(secs => %s)
            """,
            (email, WINDOW_SECONDS),
        )
        count = cur.fetchone()[0]

    if count > MAX_ATTEMPTS:
        raise HTTPException(
            429,
            "too many booking attempts, try again later",
            headers={"Retry-After": str(WINDOW_SECONDS)},
        )
        
#idempotency
BOOKING_COLS = "id, slot_id, name, email, status, created_at"

def booking_json(row) -> dict:
    return {
        "id": row[0],
        "slot_id": row[1],
        "name": row[2],
        "email": row[3],
        "status": row[4],
        "created_at": row[5].isoformat(),
    }

def request_hash(body: NewBooking) -> str:
    # Hash the cleaned values so "Ada " and "ada" with the same email count as the same body
    payload = json.dumps(
        {"slot_id": body.slot_id, "name": body.name, "email": body.email},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()

def find_by_idempotency_key(key: str):
    # Returns (stored_hash, booking) or (None, None)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT request_hash, booking_id FROM idempotency_keys WHERE key = %s", (key,))
        row = cur.fetchone()
        if row is None:
            return None, None
        cur.execute(f"SELECT {BOOKING_COLS} FROM bookings WHERE id = %s", (row[1],))
        return row[0], booking_json(cur.fetchone())