#!/usr/bin/env python3
"""
Polymarket Whale Tracker
Polls the public Polymarket Data API for large trades, prints them to the
terminal and optionally forwards them to Telegram and Discord.
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

DEFAULT_API_URL = "https://data-api.polymarket.com/v2"
MAX_FETCH_LIMIT = 1000  # hard limit of the Data API
USER_AGENT = "polymarket-whales/2.0 (+https://github.com/al1enjesus/polymarket-whales)"

# Fields every trade must carry. If Polymarket renames one of them we stop
# with a clear message instead of silently reporting "no whales".
REQUIRED_TRADE_KEYS = ("condition_id", "side", "size", "price", "timestamp")

EXPORT_FIELDS = [
    "timestamp", "market", "outcome", "side", "amount_usd", "price", "shares",
    "wallet", "trader", "market_url", "transaction_hash",
]


class ApiShapeError(Exception):
    """The API answered, but not in the format this tool understands."""


# ─────────────────────────────────────────────
# Config loader
# ─────────────────────────────────────────────
def load_config(path: str = "config.yaml") -> dict:
    """Load configuration from YAML file, with env var overrides."""
    config = {
        "min_trade_size": 500,
        "check_interval": 30,
        "fetch_limit": 500,
        "telegram": {
            "bot_token": "",
            "chat_id": "",
        },
        "discord": {
            "webhook_url": "",
        },
        "polymarket": {
            "api_url": DEFAULT_API_URL,
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
                    elif val is not None:
                        config[key] = val

    # Environment variable overrides (takes priority over YAML)
    if os.getenv("TELEGRAM_BOT_TOKEN"):
        config["telegram"]["bot_token"] = os.getenv("TELEGRAM_BOT_TOKEN")
    if os.getenv("TELEGRAM_CHAT_ID"):
        config["telegram"]["chat_id"] = os.getenv("TELEGRAM_CHAT_ID")
    if os.getenv("DISCORD_WEBHOOK_URL"):
        config["discord"]["webhook_url"] = os.getenv("DISCORD_WEBHOOK_URL")
    if os.getenv("MIN_TRADE_SIZE"):
        config["min_trade_size"] = float(os.getenv("MIN_TRADE_SIZE"))
    if os.getenv("CHECK_INTERVAL"):
        config["check_interval"] = int(os.getenv("CHECK_INTERVAL"))
    if os.getenv("POLYMARKET_API_URL"):
        config["polymarket"]["api_url"] = os.getenv("POLYMARKET_API_URL")

    # Configs written for version 1 point at the CLOB API, whose /trades
    # endpoint now requires an API key. Fall back to the public Data API.
    api_url = str(config["polymarket"]["api_url"]).rstrip("/")
    if "clob.polymarket.com" in api_url or "clob-relay" in api_url:
        logger.warning(
            "polymarket.api_url points at the CLOB API (%s), which no longer "
            "serves trades without an API key. Using %s instead.",
            api_url, DEFAULT_API_URL,
        )
        api_url = DEFAULT_API_URL
    config["polymarket"]["api_url"] = api_url

    return config


# ─────────────────────────────────────────────
# Polymarket API
# ─────────────────────────────────────────────
def fetch_recent_trades(api_url: str, min_usd: float, limit: int = 500) -> list:
    """
    Fetch the most recent trades of at least `min_usd` from the public
    Polymarket Data API (newest first). The size filter runs on the server.

    Raises requests.RequestException on network/HTTP errors and
    ApiShapeError when the response format is not the expected one.
    """
    url = f"{api_url}/trades"
    params = {
        "limit": max(1, min(int(limit), MAX_FETCH_LIMIT)),
        "filterType": "CASH",
        "filterAmount": min_usd,
        "takerOnly": "true",  # one row per fill instead of maker + taker
    }
    resp = requests.get(url, params=params, timeout=15,
                        headers={"User-Agent": USER_AGENT})
    if resp.status_code == 429:
        # Rate limited: honour Retry-After (capped) before reporting it.
        try:
            wait = min(int(resp.headers.get("Retry-After", "0")), 300)
        except ValueError:
            wait = 0
        if wait:
            logger.warning(f"Rate limited by Polymarket, waiting {wait}s.")
            time.sleep(wait)
    resp.raise_for_status()

    try:
        data = resp.json()
    except ValueError:
        raise ApiShapeError(f"{url} did not return JSON: {resp.text[:120]!r}")

    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        got = sorted(data.keys()) if isinstance(data, dict) else type(data).__name__
        raise ApiShapeError(
            f"{url} returned an unexpected response (expected an object with a "
            f"'data' list, got {got}). The Polymarket API may have changed."
        )

    trades = data["data"]
    if trades:
        missing = [k for k in REQUIRED_TRADE_KEYS if k not in trades[0]]
        if missing:
            raise ApiShapeError(
                f"{url} returned trades without the field(s) {missing}; "
                f"fields present: {sorted(trades[0].keys())}. "
                "The Polymarket API may have changed."
            )
    return trades


# ─────────────────────────────────────────────
# Trade processing
# ─────────────────────────────────────────────
def parse_trade_usd_size(trade: dict) -> float:
    """USD value of a trade: outcome shares * price per share."""
    try:
        return float(trade.get("size", 0)) * float(trade.get("price", 0))
    except (TypeError, ValueError):
        return 0.0


def trade_unique_id(trade: dict) -> str:
    """
    Identifier used to avoid duplicate alerts. One transaction can contain
    several fills, so the transaction hash alone is not enough.
    """
    return "|".join(str(trade.get(k, "")) for k in (
        "transaction_hash", "token_id", "proxy_wallet", "size", "price", "timestamp",
    ))


def format_timestamp(ts_raw) -> str:
    """Trade timestamp (unix seconds or ISO string) as 'YYYY-MM-DD HH:MM:SS' UTC."""
    try:
        if isinstance(ts_raw, (int, float)):
            return datetime.fromtimestamp(ts_raw, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        if ts_raw:
            return str(ts_raw)[:19].replace("T", " ")
    except (OverflowError, OSError, ValueError):
        pass
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def market_url(trade: dict) -> str:
    slug = trade.get("slug") or ""
    event_slug = trade.get("event_slug") or ""
    if event_slug and slug:
        return f"https://polymarket.com/event/{event_slug}/{slug}"
    if slug:
        return f"https://polymarket.com/market/{slug}"
    return ""


def normalize_trade(trade: dict) -> dict:
    """Reduce a raw API trade to the fields we print, send and export."""
    condition_id = str(trade.get("condition_id") or "")
    wallet = str(trade.get("proxy_wallet") or "")
    try:
        price = float(trade.get("price", 0))
        shares = float(trade.get("size", 0))
    except (TypeError, ValueError):
        price, shares = 0.0, 0.0
    return {
        "timestamp": format_timestamp(trade.get("timestamp")),
        "market": trade.get("title") or f"Market {condition_id[:10]}...",
        "outcome": str(trade.get("outcome") or ""),
        "side": str(trade.get("side") or "?").upper(),  # BUY or SELL
        "amount_usd": round(shares * price, 2),
        "price": price,
        "shares": shares,
        "wallet": wallet,
        "trader": trade.get("name") or trade.get("pseudonym") or "",
        "market_url": market_url(trade),
        "transaction_hash": trade.get("transaction_hash") or "",
    }


def trade_label(t: dict) -> str:
    """'BUY Yes', 'SELL Lakers', or just the side when the outcome is unknown."""
    return f"{t['side']} {t['outcome']}".strip()


def short_wallet(wallet: str) -> str:
    return f"{wallet[:6]}...{wallet[-4:]}" if len(wallet) > 12 else wallet


def trader_label(t: dict) -> str:
    wallet = short_wallet(t["wallet"])
    name = t["trader"]
    # Accounts without a profile get an auto-generated "0x...-<digits>" name.
    if not name or name.lower().startswith("0x"):
        return wallet or "unknown"
    return f"{name} ({wallet})" if wallet else name


# ─────────────────────────────────────────────
# Formatting
# ─────────────────────────────────────────────
DIVIDER = "─" * 43


def format_terminal_alert(t: dict) -> str:
    """Format a colorful terminal alert message."""
    side_color = Fore.GREEN if t["side"] == "BUY" else Fore.RED
    lines = [
        f"\n{Fore.CYAN}🐋 WHALE ALERT{Style.RESET_ALL}  {Fore.YELLOW}{t['timestamp']} UTC{Style.RESET_ALL}",
        f"{Fore.WHITE}{DIVIDER}{Style.RESET_ALL}",
        f"{Fore.WHITE}Market:{Style.RESET_ALL} {t['market']}",
        f"{Fore.WHITE}Trade: {Style.RESET_ALL} {side_color}{trade_label(t)}{Style.RESET_ALL}",
        f"{Fore.WHITE}Amount:{Style.RESET_ALL} {Fore.YELLOW}${t['amount_usd']:,.2f}{Style.RESET_ALL}"
        f"  ({t['shares']:,.0f} shares @ {t['price']:.3f})",
        f"{Fore.WHITE}Trader:{Style.RESET_ALL} {trader_label(t)}",
    ]
    if t["market_url"]:
        lines.append(f"{Fore.WHITE}Link:  {Style.RESET_ALL} {t['market_url']}")
    lines.append(f"{Fore.WHITE}{DIVIDER}{Style.RESET_ALL}")
    return "\n".join(lines)


def format_plain_message(t: dict) -> str:
    """
    Plain-text alert used for Telegram and Discord. No Markdown on purpose:
    market titles and trader names contain characters such as '_' and '*'
    that break Markdown parsing and make the send fail.
    """
    lines = [
        f"🐋 WHALE ALERT  {t['timestamp']} UTC",
        f"Market: {t['market']}",
        f"Trade: {trade_label(t)}",
        f"Amount: ${t['amount_usd']:,.2f} ({t['shares']:,.0f} shares @ {t['price']:.3f})",
        f"Trader: {trader_label(t)}",
    ]
    if t["market_url"]:
        lines.append(t["market_url"])
    return "\n".join(lines)


# ─────────────────────────────────────────────
# Senders
# ─────────────────────────────────────────────
def send_telegram_alert(bot_token: str, chat_id: str, message: str) -> bool:
    """Send a message via Telegram Bot API. Returns True on success."""
    if not bot_token or not chat_id:
        logger.debug("Telegram not configured, skipping alert.")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            # Do not log the exception text: it contains the URL with the token.
            logger.warning(f"Telegram returned HTTP {resp.status_code}: {resp.text[:200]}")
            return False
        return True
    except requests.RequestException as e:
        logger.warning(f"Failed to send Telegram alert: {type(e).__name__}")
        return False


def send_discord_alert(webhook_url: str, message: str) -> bool:
    """Send a message via Discord Webhook API. Returns True on success."""
    if not webhook_url:
        logger.debug("Discord not configured, skipping alert.")
        return False

    try:
        resp = requests.post(webhook_url, json={"content": message}, timeout=10)
        if resp.status_code >= 300:
            logger.warning(f"Discord returned HTTP {resp.status_code}: {resp.text[:200]}")
            return False
        return True
    except requests.RequestException as e:
        # The webhook URL is a secret, keep it out of the log.
        logger.warning(f"Failed to send Discord alert: {type(e).__name__}")
        return False


def export_trade(file_path: str, trade_data: dict) -> None:
    """Append one whale trade to a CSV or JSON file."""
    if not file_path:
        return

    row = {k: trade_data.get(k, "") for k in EXPORT_FIELDS}
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".csv":
        file_exists = os.path.isfile(file_path) and os.path.getsize(file_path) > 0
        with open(file_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=EXPORT_FIELDS)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
    elif ext == ".json":
        all_data = []
        if os.path.isfile(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    all_data = json.load(f)
            except (json.JSONDecodeError, ValueError):
                all_data = []

        all_data.append(row)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(all_data, f, indent=2, ensure_ascii=False)
    else:
        logger.warning(f"Unsupported export format: {ext}. Use .csv or .json.")


# ─────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────
def run(config: dict, export_path: str = None, once: bool = False) -> int:
    """
    Monitoring loop. With once=True: fetch one batch, print (and export) the
    matching trades, send nothing to Telegram/Discord, and return an exit code.
    """
    min_size = float(config["min_trade_size"])
    interval = max(1, int(config["check_interval"]))
    limit = max(1, min(int(config["fetch_limit"]), MAX_FETCH_LIMIT))
    api_url = config["polymarket"]["api_url"]
    bot_token = config["telegram"]["bot_token"]
    chat_id = config["telegram"]["chat_id"]
    discord_webhook = config["discord"]["webhook_url"]

    telegram_enabled = bool(bot_token and chat_id and
                            bot_token != "YOUR_BOT_TOKEN" and
                            chat_id != "YOUR_CHAT_ID") and not once
    discord_enabled = bool(discord_webhook and
                           discord_webhook != "YOUR_DISCORD_WEBHOOK_URL") and not once

    print(f"\n{Fore.CYAN}{'═' * 50}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}🐋  Polymarket Whale Tracker{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'═' * 50}{Style.RESET_ALL}")
    print(f"  Min trade size : {Fore.YELLOW}${min_size:,.0f}{Style.RESET_ALL}")
    if once:
        print(f"  Mode           : {Fore.YELLOW}--once (print only, no alerts sent){Style.RESET_ALL}")
    else:
        print(f"  Check interval : {Fore.YELLOW}{interval}s{Style.RESET_ALL}")
        print(f"  Telegram alerts: {Fore.GREEN+'ON' if telegram_enabled else Fore.RED+'OFF'}{Style.RESET_ALL}")
        print(f"  Discord alerts : {Fore.GREEN+'ON' if discord_enabled else Fore.RED+'OFF'}{Style.RESET_ALL}")
    if export_path:
        print(f"  Export file    : {Fore.YELLOW}{export_path}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'═' * 50}{Style.RESET_ALL}\n")

    if not once and not (telegram_enabled or discord_enabled):
        logger.info("Alerts not configured, terminal-only mode.")

    seen_ids: set = set()
    first_run = True

    while True:
        try:
            trades = fetch_recent_trades(api_url, min_size, limit)
        except ApiShapeError as e:
            logger.error(f"Polymarket API format problem: {e}")
            if once:
                return 2
            trades = None
        except requests.RequestException as e:
            logger.warning(f"Could not fetch trades: {e}")
            if once:
                return 1
            trades = None

        if trades is None:
            # Keep seen_ids: a failed poll must not cause duplicate alerts later.
            time.sleep(interval)
            continue

        new_seen: set = set()
        whale_count = 0

        # The API returns newest first; alert in chronological order.
        for trade in reversed(trades):
            trade_id = trade_unique_id(trade)
            new_seen.add(trade_id)

            # On the first poll just remember what is already there
            # (unless --once, where the current batch is the whole point).
            if first_run and not once:
                continue
            if trade_id in seen_ids:
                continue
            # The server filters by size already; this guards against the
            # filter being ignored after an API change.
            if parse_trade_usd_size(trade) < min_size:
                continue

            whale_count += 1
            t = normalize_trade(trade)

            print(format_terminal_alert(t))

            if export_path:
                try:
                    export_trade(export_path, t)
                except OSError as e:
                    logger.warning(f"Could not write {export_path}: {e}")

            if telegram_enabled or discord_enabled:
                message = format_plain_message(t)
                if telegram_enabled:
                    send_telegram_alert(bot_token, chat_id, message)
                if discord_enabled:
                    send_discord_alert(discord_webhook, message)

        if once:
            print(f"\nShown: the {whale_count} most recent trade(s) of ${min_size:,.0f}+.")
            return 0

        if not first_run and len(trades) >= limit and whale_count >= limit:
            logger.warning(
                f"All {limit} fetched trades were new, so some trades between polls "
                "were probably missed. Raise min_trade_size or fetch_limit, or lower check_interval."
            )

        # Trades that fell out of the fetched window cannot come back, so the
        # latest window is enough to remember (keeps the set bounded).
        seen_ids = new_seen
        if first_run:
            logger.info(f"Watching for new trades of ${min_size:,.0f}+ ...")
        elif whale_count == 0:
            logger.info(f"No whale trades found this cycle. Sleeping {interval}s...")
        first_run = False

        time.sleep(interval)


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Polymarket Whale Tracker")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config file")
    parser.add_argument("--export", help="Append whale trades to this file (.csv or .json)")
    parser.add_argument("--min-size", type=float, help="Minimum trade size in USD (overrides config and env)")
    parser.add_argument("--limit", type=int,
                        help="How many recent matching trades to fetch per poll, max 1000 "
                             "(default: fetch_limit from config, or 20 with --once)")
    parser.add_argument("--once", action="store_true",
                        help="Fetch once, print the matching recent trades and exit. "
                             "Never sends Telegram/Discord messages.")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if args.min_size is not None:
        config["min_trade_size"] = args.min_size
    if args.limit is not None:
        config["fetch_limit"] = args.limit
    elif args.once:
        config["fetch_limit"] = 20

    try:
        return run(config, export_path=args.export, once=args.once)
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Whale Tracker stopped.{Style.RESET_ALL}\n")
        return 0


if __name__ == "__main__":
    sys.exit(main())
