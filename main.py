import os
from fastapi import FastAPI, Depends, HTTPException, Header
from pydantic import BaseModel
from datetime import datetime
from db import get_conn
import uuid
from fastapi.responses import JSONResponse

#create the app instance
app = FastAPI()

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
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

@app.post("/reset")
async def reset_slots(body: ResetRequest):
    with get_conn() as conn:
        conn.execute("DELETE FROM bookings")
        conn.execute("DELETE FROM slots")
        for slot in body.slots:
            conn.execute(
            "INSERT INTO slots (id, start_time, end_time, provider) VALUES (%s, %s, %s, %s)",
            (slot.id, slot.start, slot.end, slot.provider),
        )
    return {
        "message": "Done"
        }

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


class NewBooking(BaseModel):
    slot_id: str
    name: str
    email: str

@app.post("/bookings", status_code=201, dependencies=[Depends(check_api_key)])
async def create_booking(body: NewBooking):
    with get_conn() as conn:
        slot = conn.execute(
            "SELECT 1 FROM slots WHERE id = %s", (body.slot_id,)
        ).fetchone()
        if slot is None:
            return JSONResponse(status_code=404, content={"error": "slot not found"})

        taken = conn.execute(
            "SELECT 1 FROM bookings WHERE slot_id = %s AND status = 'confirmed'",
            (body.slot_id,),
        ).fetchone()
        if taken is not None:
            return JSONResponse(status_code=409, content={"error": "slot already booked"})

        booking_id = str(uuid.uuid4())
        row = conn.execute(
            """
            INSERT INTO bookings (id, slot_id, name, email, status, created_at)
            VALUES (%s, %s, %s, %s, 'confirmed', NOW())
            RETURNING created_at
            """,
            (booking_id, body.slot_id, body.name, body.email),
        ).fetchone()

    return {
        "id": booking_id,
        "slot_id": body.slot_id,
        "name": body.name,
        "email": body.email,
        "status": "confirmed",
        "created_at": row[0].isoformat(),
    }

@app.get("/bookings/{booking_id}", dependencies=[Depends(check_api_key)])
async def get_booking(booking_id: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT slot_id, name, email, status, created_at FROM bookings WHERE id = %s",
            (booking_id,),
        ).fetchone()
        if row is None:
            return JSONResponse(status_code=404, content={"error": "booking not found"})

    return {
        "id": booking_id,
        "slot_id": row[0],
        "name": row[1],
        "email": row[2],
        "status": row[3],
        "created_at": row[4].isoformat(),
    }