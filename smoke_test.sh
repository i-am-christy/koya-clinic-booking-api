#!/usr/bin/env bash
BASE="${BASE:-http://127.0.0.1:8000}"
KEY="${API_KEY:?set API_KEY first}"
H=(-H "X-API-Key: $KEY" -H "Content-Type: application/json")

echo "1. Health (expect 200)"
curl -s -o /dev/null -w "%{http_code}\n" "$BASE/"

echo "2. No key (expect 401)"
curl -s -o /dev/null -w "%{http_code}\n" "$BASE/slots"

echo "3. Reset with reset.json (expect 200)"
curl -s -o /dev/null -w "%{http_code}\n" -X POST "$BASE/reset" "${H[@]}" -d @reset.json

SLOT1=$(curl -s "$BASE/slots" "${H[@]}" | python3 -c "import sys,json; print(json.load(sys.stdin)['slots'][0]['id'])")
SLOT2=$(curl -s "$BASE/slots" "${H[@]}" | python3 -c "import sys,json; print(json.load(sys.stdin)['slots'][1]['id'])")

echo "4. Book slot 1 (expect 201)"
curl -s -w "\n%{http_code}\n" -X POST "$BASE/bookings" "${H[@]}" -H "Idempotency-Key: test-1" \
  -d "{\"slot_id\":\"$SLOT1\",\"name\":\"Ada\",\"email\":\"ada@example.com\"}"

echo "5. Retry same key (expect 201 and the same id as step 4)"
curl -s -w "\n%{http_code}\n" -X POST "$BASE/bookings" "${H[@]}" -H "Idempotency-Key: test-1" \
  -d "{\"slot_id\":\"$SLOT1\",\"name\":\"Ada\",\"email\":\"ada@example.com\"}"

echo "6. New key, same slot (expect 409)"
curl -s -w "\n%{http_code}\n" -X POST "$BASE/bookings" "${H[@]}" -H "Idempotency-Key: test-2" \
  -d "{\"slot_id\":\"$SLOT1\",\"name\":\"Bob\",\"email\":\"bob@example.com\"}"

echo "7. Bad email (expect 400 with an error field)"
curl -s -w "\n%{http_code}\n" -X POST "$BASE/bookings" "${H[@]}" -H "Idempotency-Key: test-3" \
  -d "{\"slot_id\":\"$SLOT2\",\"name\":\"Cy\",\"email\":\"not-an-email\"}"

echo "8. Race: 10 simultaneous bookings on slot 2 (expect one 201 and nine 409)"
rm -f race.txt
for i in $(seq 1 10); do
  ( curl -s -o /dev/null -w "%{http_code}\n" -X POST "$BASE/bookings" "${H[@]}" \
      -H "Idempotency-Key: race-$i" \
      -d "{\"slot_id\":\"$SLOT2\",\"name\":\"Racer $i\",\"email\":\"racer$i@example.com\"}" >> race.txt ) &
done
wait
sort race.txt | uniq -c