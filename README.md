# Clinic Booking API

A booking API for a clinic, with a small web page that uses it. Patients can see open appointment slots, book one, and look up their bookings.

Built for the Koya Cohort 4 Task 1 assessment.

## Links

- **Live API:** https://koya-clinic-booking-api-k1f9.onrender.com
- **Web page:** https://koya-clinic-booking-api-k1f9.onrender.com/app
- **Interactive API docs:** https://koya-clinic-booking-api-k1f9.onrender.com/docs
- **Spec:** koyaacademy.com/assessment-api

## Features

- List open appointment slots, with an optional provider filter
- Book a slot with a name and email
- Look up a booking by its ID
- Look up one person's bookings, paged with cursors
- Cancel a booking, which frees the slot
- Idempotency keys, so a retried booking request never creates a duplicate
- Rate limiting of 5 booking attempts per email per minute
- API key required on every request

## Tech stack

- Python 3.11
- FastAPI and Uvicorn
- Neon (hosted PostgreSQL) with psycopg
- Render for hosting
- A single HTML page with plain JavaScript and CSS

## Project structure

```
clinic-api/
├── main.py            # API routes, models, validation and error handling
├── db.py              # Database connection
├── index.html         # Web page served at /app
├── requirements.txt   # Python dependencies
├── smoke_test.sh      # Runs the main booking checks
├── race.py            # Sends simultaneous bookings for one slot
├── spec_checks.sh     # Checks idempotency, cancellation, rate limit and paging
└── .env               # Local settings (not committed)
```

## Setup

Requires Python 3.11 or newer.

```bash
git clone <your-repo-url>
cd clinic-api
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file in `clinic-api`:

```
DATABASE_URL=postgresql://user:password@host/dbname?sslmode=require
API_KEY=choose-a-long-random-key
```

## Database setup

Run the table definitions in the Neon SQL Editor. The schema is in `schema.sql`, or see the spec for the table layout.

## Running locally

```bash
uvicorn main:app --reload
```

Then open:
- http://127.0.0.1:8000/docs to try the endpoints
- http://127.0.0.1:8000/app for the web page

## Endpoints

Every request except `/` needs the `X-API-Key` header.

| Method | Path | Description |
|---|---|---|
| GET | `/` | Health check |
| POST | `/reset` | Replace all slots (deletes existing bookings) |
| GET | `/slots` | List slots with an `available` flag. Optional `?provider=` |
| POST | `/bookings` | Book a slot. Accepts an optional `Idempotency-Key` header |
| GET | `/bookings?email=...` | List a person's bookings, oldest first. Paged with `limit` and `cursor` |
| GET | `/bookings/{id}` | Get one booking |
| DELETE | `/bookings/{id}` | Cancel a booking |
| GET | `/app` | Web page |

Errors return JSON in the form `{"error": "message"}`.

## Testing

With the server running, set your key and run:

```bash
export API_KEY=your-key
bash smoke_test.sh                # main booking flow
python race.py <free_slot_id>     # simultaneous bookings for one slot
bash spec_checks.sh               # idempotency, cancel, rate limit, paging
```

`smoke_test.sh` and `spec_checks.sh` call `/reset`, which deletes all existing data. Don't run them against a database you want to keep.

## Deployment

The app runs as a Render web service connected to this repository.

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
- **Environment variables:** `DATABASE_URL` and `API_KEY`

Render redeploys on each push to the main branch.

## Security

- `.env` is listed in `.gitignore` and is never committed.
- The API key is set through environment variables, not in the code.