# flights.to.CPT

Automated daily check of return flights London <-> Cape Town, filtered to fares that include
at least 1 checked bag, max 1 stop per direction, layover under 6 hours. Pushes results to
Telegram every day via GitHub Actions.

## Setup

### 1. Duffel API key
You've got one already. Note: `test_...` keys work fine for search (Offer Requests) —
you don't need a `live_...` key unless you plan to actually book through this. Test mode
returns real, live fares.

### 2. Telegram bot
1. Message **@BotFather** on Telegram, send `/newbot`, follow the prompts.
2. You'll get a token like `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` — that's `TELEGRAM_BOT_TOKEN`.
3. Message your new bot anything (e.g. "hi") so it can see your chat.
4. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser — find `"chat":{"id":...}` in the JSON. That number is `TELEGRAM_CHAT_ID`.

### 3. GitHub repo secrets
In this repo: **Settings → Secrets and variables → Actions → New repository secret**. Add:
- `DUFFEL_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

### 4. Push this code
```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/LukeNoggs/flights.to.CPT.git
git push -u origin main
```

### 5. Test it manually
Go to the **Actions** tab → "Daily flight check" → **Run workflow**. Check your Telegram.

## How it works
- Searches all 4 London airports (LHR, LGW, STN, LCY) → Cape Town, outbound 28 Nov–10 Dec 2026,
  return 6–14 Jan 2027, restricted to trip lengths of 28–50 nights to keep the search grid sane
  (full cross-product would be 117+ calls/day per origin).
- Filters out anything without a checked bag, more than 1 stop per leg, or a layover over 6h.
- Sorts by price, sends you the top 5 + day-over-day price delta on the cheapest.
- Tracks the last cheapest price in `last_price.json`, committed back to the repo each run.

## Config
All tunable via env vars in `daily.yml` if you want to widen/narrow the search — see the top of
`search_flights.py` for the full list (origins, dates, layover limit, etc).

## Cost note
This uses Duffel's **Offer Request** endpoint, which is search only — no charge unless you
actually book. ~40-60 API calls/day depending on how many date pairs fall in the trip-length
window; well within any reasonable rate limit.
