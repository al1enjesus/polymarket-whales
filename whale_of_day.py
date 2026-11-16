#!/usr/bin/env python3
"""
Whale of the Day: one daily summary message.
Picks the active Polymarket market with the highest 24h volume (skipping
markets that are already priced near 0 or 1) and posts it to Telegram.

Usage:
    python whale_of_day.py --dry-run     # print the message, send nothing
    python whale_of_day.py               # needs TELEGRAM_BOT_TOKEN and
                                         # WHALE_CHANNEL_ID (or TELEGRAM_CHAT_ID)
"""

import os, sys, json, datetime, urllib.request, urllib.error
from dotenv import load_dotenv

load_dotenv()

GAMMA_API = "https://gamma-api.polymarket.com"


def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "polymarket-whales/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def get_top_markets():
    url = f"{GAMMA_API}/markets?limit=50&active=true&closed=false&order=volume24hr&ascending=false"
    markets = fetch(url)
    if not isinstance(markets, list) or (markets and "volume24hr" not in markets[0]):
        raise RuntimeError(
            "Unexpected response from the Gamma API (expected a list of markets "
            "with a 'volume24hr' field). The Polymarket API may have changed."
        )
    return markets


def _pick(m):
    return {
        "question": m.get("question", "Unknown market"),
        "slug": m.get("slug", ""),
        "vol24": float(m.get("volume24hr") or 0),
        "vol_total": float(m.get("volume") or 0),
        "price": float(m.get("lastTradePrice") or 0),
        "endDate": m.get("endDate", ""),
    }


def get_biggest_whale_market(markets):
    """Market with the biggest 24h volume whose price is not already near 0 or 1."""
    candidates = [_pick(m) for m in markets]
    open_ones = [c for c in candidates
                 if c["vol24"] >= 100_000 and 0.02 <= c["price"] <= 0.98]
    pool = open_ones or candidates  # fallback: top by volume regardless of price
    return max(pool, key=lambda c: c["vol24"]) if pool else None


def format_message(market):
    end_date = market.get("endDate", "")
    try:
        dt = datetime.datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        closes = dt.strftime("%b %d, %Y")
    except ValueError:
        closes = end_date[:10] if end_date else "TBD"

    slug = market["slug"]
    market_url = f"https://polymarket.com/market/{slug}" if slug else "https://polymarket.com"
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%b %d, %Y")

    # Plain text on purpose: market titles can contain Markdown characters.
    return (
        f"🐋 Whale of the Day, {today}\n\n"
        f"{market['question']}\n\n"
        f"Last trade price: {market['price']:.3f}\n"
        f"24h volume: ${market['vol24']:,.0f}\n"
        f"Total volume: ${market['vol_total']:,.0f}\n"
        f"Closes: {closes}\n\n"
        f"{market_url}"
    )


def send_telegram(bot_token, chat_id, msg):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": msg}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def main():
    dry_run = "--dry-run" in sys.argv[1:]
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("WHALE_CHANNEL_ID") or os.getenv("TELEGRAM_CHAT_ID", "")
    if not dry_run and not (bot_token and chat_id):
        print("TELEGRAM_BOT_TOKEN and WHALE_CHANNEL_ID (or TELEGRAM_CHAT_ID) must be set. "
              "Use --dry-run to preview the message without sending.", file=sys.stderr)
        return 1

    try:
        markets = get_top_markets()
    except (urllib.error.URLError, RuntimeError, ValueError) as e:
        print(f"Could not load markets: {e}", file=sys.stderr)
        return 1

    whale = get_biggest_whale_market(markets)
    if not whale:
        print("No suitable market found", file=sys.stderr)
        return 1

    msg = format_message(whale)
    print(msg)

    if dry_run:
        print("\n(dry run, nothing sent)")
        return 0

    try:
        result = send_telegram(bot_token, chat_id, msg)
    except urllib.error.HTTPError as e:
        # Do not print the request URL: it contains the bot token.
        print(f"Telegram returned HTTP {e.code}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"Could not reach Telegram: {e.reason}", file=sys.stderr)
        return 1
    if not result.get("ok"):
        print(f"Telegram error: {result.get('description')}", file=sys.stderr)
        return 1
    print(f"\nPosted, message_id={result['result']['message_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
