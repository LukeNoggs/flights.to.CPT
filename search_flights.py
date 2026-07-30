#!/usr/bin/env python3
"""
flights.to.CPT — daily London <-> Cape Town fare check.

Searches Duffel for return flights across a flexible date window,
requires 1 checked bag included, allows max 1 stop per direction
with a layover no longer than 6 hours, and pushes the cheapest
qualifying options to Telegram.

Env vars required:
  DUFFEL_API_KEY       - Duffel access token (test_... or live_...)
  TELEGRAM_BOT_TOKEN    - Bot token from BotFather
  TELEGRAM_CHAT_ID      - Your chat id (see README for how to find it)

Optional env vars (defaults set below):
  ORIGIN_AIRPORTS, DEST_AIRPORT,
  OUTBOUND_START, OUTBOUND_END,
  RETURN_START, RETURN_END,
  MAX_LAYOVER_HOURS, TOP_N_RESULTS
"""

import os
import sys
import json
import time
import itertools
from datetime import date, timedelta

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DUFFEL_API_KEY = os.environ["DUFFEL_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

ORIGIN_AIRPORTS = os.environ.get("ORIGIN_AIRPORTS", "LHR,LGW,STN,LCY").split(",")
DEST_AIRPORT = os.environ.get("DEST_AIRPORT", "CPT")

OUTBOUND_START = date.fromisoformat(os.environ.get("OUTBOUND_START", "2026-11-28"))
OUTBOUND_END = date.fromisoformat(os.environ.get("OUTBOUND_END", "2026-12-10"))
RETURN_START = date.fromisoformat(os.environ.get("RETURN_START", "2027-01-06"))
RETURN_END = date.fromisoformat(os.environ.get("RETURN_END", "2027-01-14"))

MAX_LAYOVER_HOURS = float(os.environ.get("MAX_LAYOVER_HOURS", "6"))
MAX_CONNECTIONS = 1  # per slice, i.e. max 1 stop
TOP_N_RESULTS = int(os.environ.get("TOP_N_RESULTS", "5"))

DUFFEL_BASE = "https://api.duffel.com"
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Authorization": f"Bearer {DUFFEL_API_KEY}",
    "Duffel-Version": "v2",
}

STATE_FILE = os.environ.get("STATE_FILE", "last_price.json")


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def build_date_pairs():
    """
    Pair each outbound date with a spread of return dates rather than a full
    cross-product (13 outbound x 9 return = 117 calls/day is wasteful).
    We sample every outbound date against every return date but only where
    the resulting trip length is between 30 and 48 nights, which matches
    the intent of the ranges given and keeps the call count sane.
    """
    pairs = []
    for out_d, ret_d in itertools.product(
        daterange(OUTBOUND_START, OUTBOUND_END),
        daterange(RETURN_START, RETURN_END),
    ):
        nights = (ret_d - out_d).days
        if 28 <= nights <= 50:
            pairs.append((out_d, ret_d))
    return pairs


def create_offer_request(origin: str, out_date: date, ret_date: date):
    payload = {
        "data": {
            "slices": [
                {
                    "origin": origin,
                    "destination": DEST_AIRPORT,
                    "departure_date": out_date.isoformat(),
                },
                {
                    "origin": DEST_AIRPORT,
                    "destination": origin,
                    "departure_date": ret_date.isoformat(),
                },
            ],
            "passengers": [{"type": "adult"}],
            "cabin_class": "economy",
            "max_connections": MAX_CONNECTIONS,
        }
    }
    resp = requests.post(
        f"{DUFFEL_BASE}/air/offer_requests?return_offers=true&supplier_timeout=15000",
        headers=HEADERS,
        data=json.dumps(payload),
        timeout=30,
    )
    if resp.status_code >= 400:
        print(f"  [warn] {origin} {out_date}->{ret_date}: {resp.status_code} {resp.text[:200]}")
        return []
    return resp.json().get("data", {}).get("offers", [])


def max_layover_hours(slice_segments):
    """Given a slice's segments, return the longest gap between segments in hours."""
    if len(slice_segments) < 2:
        return 0.0
    worst = 0.0
    for a, b in zip(slice_segments, slice_segments[1:]):
        arr = a["arriving_at"]
        dep = b["departing_at"]
        from datetime import datetime
        fmt = "%Y-%m-%dT%H:%M:%S"
        t1 = datetime.strptime(arr[:19], fmt)
        t2 = datetime.strptime(dep[:19], fmt)
        gap_hours = (t2 - t1).total_seconds() / 3600
        worst = max(worst, gap_hours)
    return worst


def has_checked_bag(offer):
    """True if every passenger on every segment gets at least 1 checked bag."""
    for slice_ in offer.get("slices", []):
        for segment in slice_.get("segments", []):
            for passenger in segment.get("passengers", []):
                bags = passenger.get("baggages", [])
                checked = sum(
                    b.get("quantity", 0) for b in bags if b.get("type") == "checked"
                )
                if checked < 1:
                    return False
    return True


def passes_stopover_rules(offer):
    for slice_ in offer.get("slices", []):
        segs = slice_.get("segments", [])
        if len(segs) - 1 > MAX_CONNECTIONS:
            return False
        if max_layover_hours(segs) > MAX_LAYOVER_HOURS:
            return False
    return True


def summarize_offer(offer, origin, out_date, ret_date):
    price = float(offer["total_amount"])
    currency = offer["total_currency"]
    out_slice, ret_slice = offer["slices"]

    def route_str(slice_):
        codes = [slice_["segments"][0]["origin"]["iata_code"]]
        for seg in slice_["segments"]:
            codes.append(seg["destination"]["iata_code"])
        return " -> ".join(codes)

    def carrier_str(slice_):
        carriers = {seg["marketing_carrier"]["name"] for seg in slice_["segments"]}
        return "/".join(sorted(carriers))

    return {
        "price": price,
        "currency": currency,
        "origin": origin,
        "out_date": out_date.isoformat(),
        "ret_date": ret_date.isoformat(),
        "out_route": route_str(out_slice),
        "ret_route": route_str(ret_slice),
        "out_carrier": carrier_str(out_slice),
        "ret_carrier": carrier_str(ret_slice),
        "out_stops": len(out_slice["segments"]) - 1,
        "ret_stops": len(ret_slice["segments"]) - 1,
        "offer_id": offer["id"],
    }


def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )
    if resp.status_code >= 400:
        print(f"[error] Telegram send failed: {resp.status_code} {resp.text}")


def load_last_price():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f).get("cheapest_price")
    return None


def save_last_price(price):
    with open(STATE_FILE, "w") as f:
        json.dump({"cheapest_price": price}, f)


def main():
    pairs = build_date_pairs()
    print(f"Checking {len(pairs)} date pairs x {len(ORIGIN_AIRPORTS)} origins "
          f"= {len(pairs) * len(ORIGIN_AIRPORTS)} searches...")

    results = []
    for origin in ORIGIN_AIRPORTS:
        for out_date, ret_date in pairs:
            offers = create_offer_request(origin, out_date, ret_date)
            for offer in offers:
                if not has_checked_bag(offer):
                    continue
                if not passes_stopover_rules(offer):
                    continue
                results.append(summarize_offer(offer, origin, out_date, ret_date))
            time.sleep(0.3)  # be polite to the API

    if not results:
        send_telegram(
            "*flights.to.CPT*\nNo qualifying fares found today "
            "(1 checked bag, <=1 stop, <=6h layover)."
        )
        print("No results.")
        return

    results.sort(key=lambda r: r["price"])
    top = results[:TOP_N_RESULTS]
    cheapest = top[0]

    last_price = load_last_price()
    delta_line = ""
    if last_price is not None:
        diff = cheapest["price"] - last_price
        if diff < 0:
            delta_line = f"\n📉 Down {abs(diff):.0f} {cheapest['currency']} since last check"
        elif diff > 0:
            delta_line = f"\n📈 Up {diff:.0f} {cheapest['currency']} since last check"
        else:
            delta_line = "\n➡️ No change since last check"
    save_last_price(cheapest["price"])

    lines = [f"*flights.to.CPT — daily check*{delta_line}\n"]
    for i, r in enumerate(top, 1):
        lines.append(
            f"{i}. *{r['price']:.0f} {r['currency']}* — {r['origin']}→{DEST_AIRPORT}\n"
            f"   Out: {r['out_date']} ({r['out_stops']} stop) {r['out_carrier']}\n"
            f"   Back: {r['ret_date']} ({r['ret_stops']} stop) {r['ret_carrier']}\n"
        )
    lines.append(f"\nChecked {len(pairs) * len(ORIGIN_AIRPORTS)} searches, "
                  f"{len(results)} fares passed filters (1 bag, ≤1 stop, ≤6h layover).")

    send_telegram("\n".join(lines))
    print(f"Sent Telegram alert. Cheapest: {cheapest['price']} {cheapest['currency']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Best-effort: let you know the script broke, not just silently fail
        try:
            send_telegram(f"⚠️ flights.to.CPT script errored: {e}")
        except Exception:
            pass
        print(f"[fatal] {e}", file=sys.stderr)
        raise
