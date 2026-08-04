#!/usr/bin/env python3
"""
Whale of the Day — daily auto-post to @polymarketwhales_ai
Finds the market with the biggest 24h volume spike on Polymarket.
"""

import os, sys, json, datetime, urllib.request, urllib.parse
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    print("Error: TELEGRAM_BOT_TOKEN environment variable is not set.", file=sys.stderr)
    sys.exit(1)
CHAT_ID    = os.getenv("WHALE_CHANNEL_ID", "-1003518498844")
GAMMA_API  = "https://gamma-api.polymarket.com"

def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "polymarket-whales/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

def get_top_markets():
    url = f"{GAMMA_API}/markets?limit=50&active=true&order=volume24hr&ascending=false"
    return fetch(url)

def get_biggest_whale_market(markets):
    """Find market with biggest 24h volume and interesting price action."""
    best = None
    for m in markets:
        vol24 = float(m.get("volume24hr") or 0)
        vol_total = float(m.get("volume") or 0)
        price = float(m.get("lastTradePrice") or 0)
        # Skip extreme prices (already decided) and tiny markets
        if vol24 < 100_000:
            continue
        if price < 0.02 or price > 0.98:
            continue
        score = vol24
        if best is None or score > best["score"]:
            best = {
                "score": score,
                "question": m.get("question", "Unknown market"),
                "slug": m.get("slug", ""),
                "vol24": vol24,
                "vol_total": vol_total,
                "price": price,
                "conditionId": m.get("conditionId", ""),
                "endDate": m.get("endDate", ""),
                "outcomes": m.get("outcomes", ""),
            }
    # Fallback: just pick top by vol24 regardless of price
    if best is None and markets:
        m = markets[0]
        best = {
            "score": float(m.get("volume24hr") or 0),
            "question": m.get("question", "Unknown market"),
            "slug": m.get("slug", ""),
            "vol24": float(m.get("volume24hr") or 0),
            "vol_total": float(m.get("volume") or 0),
            "price": float(m.get("lastTradePrice") or 0),
            "conditionId": m.get("conditionId", ""),
            "endDate": m.get("endDate", ""),
        }
    return best

def format_message(market):
    vol24 = market["vol24"]
    vol_total = market["vol_total"]
    price = market["price"]
    question = market["question"]
    slug = market["slug"]
    end_date = market.get("endDate", "")

    # Format end date
    try:
        dt = datetime.datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        closes = dt.strftime("%b %d, %Y")
    except:
        closes = end_date[:10] if end_date else "TBD"

    # Price interpretation
    pct = price * 100
    side = "YES" if price >= 0.5 else "NO"
    side_emoji = "🟢" if side == "YES" else "🔴"
    opp_pct = 100 - pct if side == "YES" else pct

    market_url = f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com"

    today = datetime.datetime.utcnow().strftime("%b %d, %Y")

    msg = f"""🐋 *Whale of the Day* — {today}

*{question}*

{side_emoji} Market says: *{pct:.0f}% {side}*
💰 24h Volume: *${vol24/1_000_000:.1f}M*
📊 Total Volume: ${vol_total/1_000_000:.1f}M
📅 Closes: {closes}

Whales moved *${vol24/1_000_000:.1f}M* on this market today alone.

[🔗 Trade on Polymarket]({market_url})

_Track every whale move in real time 👇_
[GitHub](https://github.com/al1enjesus/polymarket-whales) · @polymarketwhales\\_ai"""

    return msg

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": CHAT_ID,
        "text": msg,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)

def main():
    print("Fetching top markets by 24h volume...")
    markets = get_top_markets()
    print(f"Got {len(markets)} markets")

    whale = get_biggest_whale_market(markets)
    if not whale:
        print("No suitable market found", file=sys.stderr)
        sys.exit(1)

    print(f"Top whale market: {whale['question'][:60]}")
    print(f"24h volume: ${whale['vol24']:,.0f}")
    print(f"Price: {whale['price']:.2%}")

    msg = format_message(whale)
    print("\n--- Message preview ---")
    print(msg)
    print("---")

    result = send_telegram(msg)
    if result.get("ok"):
        print(f"✅ Posted! msg_id={result['result']['message_id']}")
    else:
        print(f"❌ Error: {result}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
