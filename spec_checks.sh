#!/usr/bin/env bash
B="${BASE:-http://127.0.0.1:8000}"
H=(-H "X-API-Key: $API_KEY" -H "Content-Type: application/json")

echo "== Reset with 4 future slots"
curl -s -X POST "$B/reset" "${H[@]}" -d '{"slots":[
{"id":"c1","provider":"Dr Test","start":"2027-06-01T09:00:00Z","end":"2027-06-01T09:30:00Z"},
{"id":"c2","provider":"Dr Test","start":"2027-06-01T10:00:00Z","end":"2027-06-01T10:30:00Z"},
{"id":"c3","provider":"Dr Test","start":"2027-06-01T11:00:00Z","end":"2027-06-01T11:30:00Z"},
{"id":"c4","provider":"Dr Test","start":"2027-06-01T12:00:00Z","end":"2027-06-01T12:30:00Z"}]}'
echo

echo "== 1. Idempotency: same key, different body (expect 422)"
BID=$(curl -s -X POST "$B/bookings" "${H[@]}" -H "Idempotency-Key: k1" \
  -d '{"slot_id":"c1","name":"Ada","email":"ada@example.com"}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "booking id: $BID"
curl -s -w "\n%{http_code}\n" -X POST "$B/bookings" "${H[@]}" -H "Idempotency-Key: k1" \
  -d '{"slot_id":"c1","name":"Someone Else","email":"ada@example.com"}'

echo "== 2. Cancel twice (expect 200, then 409)"
curl -s -w "\n%{http_code}\n" -X DELETE "$B/bookings/$BID" "${H[@]}"
curl -s -w "\n%{http_code}\n" -X DELETE "$B/bookings/$BID" "${H[@]}"

echo "== 3. Rate limit: 6 attempts from zed@example.com (expect 6th to be 429)"
for i in 1 2 3 4 5; do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST "$B/bookings" "${H[@]}" \
    -d '{"slot_id":"c2","name":"Zed","email":"zed@example.com"}'
done
curl -i -X POST "$B/bookings" "${H[@]}" \
  -d '{"slot_id":"c2","name":"Zed","email":"zed@example.com"}'
echo

echo "== 4. Cursor paging for pat@example.com, limit=1 (expect oldest first, then null)"
# Pat books c3, c4, then c1 (c1 was freed by the cancel above), in that order
for slot in c3 c4 c1; do
  curl -s -o /dev/null -w "book $slot: %{http_code}\n" -X POST "$B/bookings" "${H[@]}" \
    -d "{\"slot_id\":\"$slot\",\"name\":\"Pat\",\"email\":\"pat@example.com\"}"
done
CUR=""
for page in 1 2 3 4; do
  RESP=$(curl -s -G "$B/bookings" "${H[@]}" \
    --data-urlencode "email=pat@example.com" --data-urlencode "limit=1" \
    --data-urlencode "cursor=$CUR")
  echo "page $page: $RESP"
  CUR=$(echo "$RESP" | python -c "import sys,json; print(json.load(sys.stdin)['next_cursor'] or '')")
  [ -z "$CUR" ] && break
done