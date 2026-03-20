#!/usr/bin/env python3
"""
🐋 Polymarket Whale Tracker
Monitors Polymarket for large trades and sends Telegram alerts.
"""

import os
import sys
import time
import logging
import requests
import yaml
import csv
import json
import argparse
from datetime import datetime, timezone
from dotenv import load_dotenv
from colorama import init, Fore, Style

# Initialize colorama for cross-platform colored output
init(autoreset=True)

# Load environment variables from .env file if present
load_dotenv()

# ─────────────────────────────────────────────
# Logging setup
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Config loader
# ─────────────────────────────────────────────
def load_config(path: str = "config.yaml") -> dict:
    """Load configuration from YAML file, with env var overrides."""
    config = {
        "min_trade_size": 500,
        "check_interval": 30,
        "telegram": {
            "bot_token": "",
            "chat_id": "",
        },
        "polymarket": {
            "api_url": "https://clob.polymarket.com",
        },
    }

    if os.path.exists(path):
        with open(path, "r") as f:
            loaded = yaml.safe_load(f)
            if loaded:
                # Deep merge
                for key, val in loaded.items():
                    if isinstance(val, dict) and key in config:
                        config[key].update(val)
                    else:
                        config[key] = val

    # Environment variable overrides (takes priority over YAML)
    if os.getenv("TELEGRAM_BOT_TOKEN"):
        config["telegram"]["bot_token"] = os.getenv("TELEGRAM_BOT_TOKEN")
    if os.getenv("TELEGRAM_CHAT_ID"):
        config["telegram"]["chat_id"] = os.getenv("TELEGRAM_CHAT_ID")
    if os.getenv("MIN_TRADE_SIZE"):
        config["min_trade_size"] = float(os.getenv("MIN_TRADE_SIZE"))

    return config


# ─────────────────────────────────────────────
# Polymarket API
# ─────────────────────────────────────────────
def fetch_recent_trades(api_url: str, limit: int = 100) -> list:
    """Fetch recent trades from Polymarket CLOB API."""
    url = f"{api_url}/trades"
    params = {"limit": limit}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        # The CLOB API returns {"data": [...], "next_cursor": ...}
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        # Some endpoints return a list directly
        if isinstance(data, list):
            return data
        return []
    except requests.exceptions.ConnectionError:
        logger.warning("⚠️  Network error: could not reach Polymarket API.")
        return []
    except requests.exceptions.Timeout:
        logger.warning("⚠️  Request timed out fetching trades.")
        return []
    except requests.exceptions.HTTPError as e:
        logger.warning(f"⚠️  HTTP error fetching trades: {e}")
        return []
    except Exception as e:
        logger.warning(f"⚠️  Unexpected error fetching trades: {e}")
        return []


def fetch_market_info(condition_id: str) -> dict:
    """
    Fetch market metadata (title, etc.) from Polymarket Gamma API.
    Returns a dict with at least 'question' key.
    """
    url = "https://gamma-api.polymarket.com/markets"
    params = {"condition_id": condition_id}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and len(data) > 0:
            return data[0]
        if isinstance(data, dict):
            return data
        return {}
    except Exception as e:
        logger.debug(f"Could not fetch market info for {condition_id}: {e}")
        return {}


# ─────────────────────────────────────────────
# Trade processing
# ─────────────────────────────────────────────
def parse_trade_usd_size(trade: dict) -> float:
    """
    Calculate the USD size of a trade.
    size * price gives approximate USD value for a YES trade;
    size * (1 - price) for a NO trade — but simpler: use size as USDC shares.
    Polymarket CLOB: 'size' is the number of outcome shares, 'price' is in USD.
    USD value = size * price (for market buys).
    """
    try:
        size = float(trade.get("size", 0))
        price = float(trade.get("price", 0))
        return size * price
    except (TypeError, ValueError):
        return 0.0


def format_side(side: str) -> str:
    """Normalize side string to YES/NO."""
    s = str(side).upper()
    if s in ("YES", "BUY", "1"):
        return "YES"
    if s in ("NO", "SELL", "0"):
        return "NO"
    return side.upper()


def trade_unique_id(trade: dict) -> str:
    """Generate a unique identifier for a trade to avoid duplicate alerts."""
    return trade.get("id") or trade.get("trade_id") or str(trade)


# ─────────────────────────────────────────────
# Formatting
# ─────────────────────────────────────────────
DIVIDER = "─" * 43


def format_terminal_alert(market_title: str, side: str, amount_usd: float,
                           price: float, timestamp: str) -> str:
    """Format a colorful terminal alert message."""
    ts = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    side_color = Fore.GREEN if side == "YES" else Fore.RED
    prob_pct = int(price * 100) if side == "YES" else int((1 - price) * 100)

    lines = [
        f"\n{Fore.CYAN}🐋 WHALE ALERT{Style.RESET_ALL}  {Fore.YELLOW}{ts}{Style.RESET_ALL}",
        f"{Fore.WHITE}{DIVIDER}{Style.RESET_ALL}",
        f"{Fore.WHITE}Market:{Style.RESET_ALL} {market_title}",
        f"{Fore.WHITE}Side:  {Style.RESET_ALL} {side_color}{side}{Style.RESET_ALL}",
        f"{Fore.WHITE}Amount:{Style.RESET_ALL} {Fore.YELLOW}${amount_usd:,.2f}{Style.RESET_ALL}",
        f"{Fore.WHITE}Price: {Style.RESET_ALL} {price:.4f} ({side_color}{prob_pct}% {side}{Style.RESET_ALL})",
        f"{Fore.WHITE}{DIVIDER}{Style.RESET_ALL}",
    ]
    return "\n".join(lines)


def format_telegram_message(market_title: str, side: str, amount_usd: float,
                             price: float, timestamp: str) -> str:
    """Format a Telegram alert message (plain text, emoji-rich)."""
    ts = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    side_emoji = "✅" if side == "YES" else "❌"
    prob_pct = int(price * 100) if side == "YES" else int((1 - price) * 100)

    return (
        f"🐋 *WHALE ALERT*  `{ts}`\n"
        f"{'─' * 30}\n"
        f"*Market:* {market_title}\n"
        f"*Side:*    {side_emoji} {side}\n"
        f"*Amount:* `${amount_usd:,.2f}`\n"
        f"*Price:*   `{price:.4f}` ({prob_pct}% {side})\n"
        f"{'─' * 30}"
    )


# ─────────────────────────────────────────────
# Telegram sender
# ─────────────────────────────────────────────
def send_telegram_alert(bot_token: str, chat_id: str, message: str) -> bool:
    """Send a message via Telegram Bot API. Returns True on success."""
    if not bot_token or not chat_id:
        logger.debug("Telegram not configured — skipping alert.")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except requests.exceptions.HTTPError as e:
        logger.warning(f"Telegram HTTP error: {e} — response: {resp.text[:200]}")
        return False
    except Exception as e:
        logger.warning(f"Failed to send Telegram alert: {e}")
        return False


def export_trade(file_path: str, trade_data: dict) -> None:
    """Export trade data to a CSV or JSON file."""
    if not file_path:
        return

    ext = os.path.splitext(file_path)[1].lower()
    
    if ext == ".csv":
        file_exists = os.path.isfile(file_path)
        with open(file_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=trade_data.keys())
            if not file_exists:
                writer.writeheader()
            writer.writerow(trade_data)
    elif ext == ".json":
        all_data = []
        if os.path.isfile(file_path):
            try:
                with open(file_path, "r") as f:
                    all_data = json.load(f)
            except (json.JSONDecodeError, ValueError):
                all_data = []
        
        all_data.append(trade_data)
        with open(file_path, "w") as f:
            json.dump(all_data, f, indent=2)
    else:
        logger.warning(f"Unsupported export format: {ext}. Use .csv or .json.")


# ─────────────────────────────────────────────
# Market info cache (avoid hammering the API)
# ─────────────────────────────────────────────
_market_cache: dict = {}


def get_market_title(condition_id: str) -> str:
    """Return market title, using a local cache to reduce API calls."""
    if condition_id in _market_cache:
        return _market_cache[condition_id]

    info = fetch_market_info(condition_id)
    title = (
        info.get("question")
        or info.get("title")
        or info.get("name")
        or f"Market {condition_id[:10]}..."
    )
    _market_cache[condition_id] = title
    return title


# ─────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────
def run(config: dict, export_path: str = None) -> None:
    """Main monitoring loop."""
    min_size = float(config["min_trade_size"])
    interval = int(config["check_interval"])
    # Allow env var override — useful for geo-restricted regions
    # Set POLYMARKET_API_URL=https://polyclawster.com/api/clob-relay to bypass geo-blocks
    api_url = os.getenv("POLYMARKET_API_URL", config["polymarket"]["api_url"])
    bot_token = config["telegram"]["bot_token"]
    chat_id = config["telegram"]["chat_id"]

    telegram_enabled = bool(bot_token and chat_id and
                            bot_token != "YOUR_BOT_TOKEN" and
                            chat_id != "YOUR_CHAT_ID")

    print(f"\n{Fore.CYAN}{'═' * 50}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}🐋  Polymarket Whale Tracker — Starting up{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'═' * 50}{Style.RESET_ALL}")
    print(f"  Min trade size : {Fore.YELLOW}${min_size:,.0f}{Style.RESET_ALL}")
    print(f"  Check interval : {Fore.YELLOW}{interval}s{Style.RESET_ALL}")
    print(f"  Telegram alerts: {Fore.GREEN+'ON' if telegram_enabled else Fore.RED+'OFF'}{Style.RESET_ALL}")
    if export_path:
        print(f"  Export path    : {Fore.YELLOW}{export_path}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'═' * 50}{Style.RESET_ALL}\n")

    if not telegram_enabled:
        logger.info("ℹ️  Telegram not configured — terminal-only mode.")

    seen_ids: set = set()
    first_run = True

    while True:
        try:
            trades = fetch_recent_trades(api_url)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            logger.error(f"Error in fetch loop: {e}")
            trades = []

        new_seen: set = set()
        whale_count = 0

        for trade in trades:
            trade_id = trade_unique_id(trade)
            new_seen.add(trade_id)

            # On first run, just populate seen_ids (don't alert on old trades)
            if first_run:
                continue

            # Skip already-seen trades
            if trade_id in seen_ids:
                continue

            # Calculate USD size and filter
            amount_usd = parse_trade_usd_size(trade)
            if amount_usd < min_size:
                continue

            whale_count += 1

            # Get trade details
            condition_id = trade.get("market") or trade.get("condition_id", "")
            side_raw = trade.get("side", trade.get("outcome", ""))
            side = format_side(side_raw)
            price = float(trade.get("price", 0))
            ts_raw = trade.get("timestamp") or trade.get("created_at", "")

            # Parse timestamp
            if ts_raw:
                try:
                    if isinstance(ts_raw, (int, float)):
                        ts = datetime.fromtimestamp(ts_raw, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        ts = str(ts_raw)[:19].replace("T", " ")
                except Exception:
                    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            else:
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

            # Fetch market title
            market_title = get_market_title(condition_id) if condition_id else "Unknown Market"

            # Print terminal alert
            print(format_terminal_alert(market_title, side, amount_usd, price, ts))

            # Send Telegram alert
            if telegram_enabled:
                tg_msg = format_telegram_message(market_title, side, amount_usd, price, ts)
                ok = send_telegram_alert(bot_token, chat_id, tg_msg)
                if ok:
                    logger.debug("✅ Telegram alert sent.")
            
            # Export if path is provided
            if export_path:
                trade_record = {
                    "timestamp": ts,
                    "market": market_title,
                    "side": side,
                    "amount_usd": round(amount_usd, 2),
                    "price": price,
                    "market_url": f"https://polymarket.com/event/{condition_id}" if condition_id else ""
                }
                export_trade(export_path, trade_record)
                logger.debug(f"💾 Trade exported to {export_path}")

        # Update seen set (keep it bounded)
        seen_ids = new_seen
        first_run = False

        if whale_count == 0 and not first_run:
            logger.info(f"No whale trades found this cycle. Sleeping {interval}s...")

        time.sleep(interval)


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Polymarket Whale Tracker")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config file")
    parser.add_argument("--export", help="Export path (CSV or JSON)")
    args = parser.parse_args()

    config = load_config(args.config)

    try:
        run(config, export_path=args.export)
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}👋 Whale Tracker stopped.{Style.RESET_ALL}\n")
        sys.exit(0)
