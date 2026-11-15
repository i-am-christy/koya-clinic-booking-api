import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

BASE = os.environ.get("BASE", "http://127.0.0.1:8000")
KEY = os.environ["API_KEY"]
SLOT_ID = sys.argv[1]

def book(i):
    body = json.dumps({
        "slot_id": SLOT_ID,
        "name": f"Racer {i}",
        "email": f"racer{i}@example.com",
    }).encode()
    req = urllib.request.Request(
        f"{BASE}/bookings",
        data=body,
        method="POST",
        headers={
            "X-API-Key": KEY,
            "Content-Type": "application/json",
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code

with ThreadPoolExecutor(max_workers=10) as pool:
    results = list(pool.map(book, range(1, 11)))

print(Counter(results))