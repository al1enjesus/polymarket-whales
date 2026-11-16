# polymarket-whales

A small Python script that watches the public Polymarket trade feed and reports every trade above a dollar threshold. It prints to the terminal and can forward the same alerts to Telegram and Discord, or append them to a CSV/JSON file.

No Polymarket account, API key or wallet is needed. It only reads public data and never places trades.

## Sample output

Captured on 16 November 2026 with `python main.py --once --min-size 10000 --limit 4`:

```
══════════════════════════════════════════════════
🐋  Polymarket Whale Tracker
══════════════════════════════════════════════════
  Min trade size : $10,000
  Mode           : --once (print only, no alerts sent)
══════════════════════════════════════════════════

🐋 WHALE ALERT  2026-11-16 04:58:26 UTC
───────────────────────────────────────────
Market: Canucks vs. Golden Knights
Trade:  SELL Canucks
Amount: $15,030.38  (15,045 shares @ 0.999)
Trader: Woaifacai (0x9b89...4244)
Link:   https://polymarket.com/event/nhl-van-las-2026-11-15/nhl-van-las-2026-11-15
───────────────────────────────────────────

🐋 WHALE ALERT  2026-11-16 05:30:13 UTC
───────────────────────────────────────────
Market: Will Germany win on 2026-11-16?
Trade:  BUY Yes
Amount: $10,580.24  (20,347 shares @ 0.520)
Trader: PM4AGG (0x4ff6...128d)
Link:   https://polymarket.com/event/unl-ger-nld-2026-11-16/unl-ger-nld-2026-11-16-ger
───────────────────────────────────────────

🐋 WHALE ALERT  2026-11-16 05:45:38 UTC
───────────────────────────────────────────
Market: Dota 2: potn9ki vs oveA - Game 2 Winner
Trade:  SELL potn9ki
Amount: $11,692.88  (11,720 shares @ 0.998)
Trader: yep.itsme (0xad2f...ac6a)
Link:   https://polymarket.com/event/dota2-p-ovea-2026-11-13/dota2-p-ovea-2026-11-13-game2
───────────────────────────────────────────

🐋 WHALE ALERT  2026-11-16 06:04:47 UTC
───────────────────────────────────────────
Market: Will Colette Kress be the next CEO of NVIDIA?
Trade:  BUY No
Amount: $13,779.73  (15,193 shares @ 0.907)
Trader: Dragontree (0x2c39...56dc)
Link:   https://polymarket.com/event/next-ceo-of-nvidia/will-colette-kress-be-the-next-ceo-of-nvidia
───────────────────────────────────────────

Shown: the 4 most recent trade(s) of $10,000+.
```

How to read an alert: `BUY No @ 0.907` means the trader bought "No" shares at 90.7 cents each. `Amount` is shares times price, in USD. Trader names and wallet addresses are the public ones Polymarket shows on its own site.

## Install and run

Tested on Python 3.12 (Linux).

```bash
git clone https://github.com/al1enjesus/polymarket-whales
cd polymarket-whales
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

python main.py --once        # show the latest large trades and exit
python main.py               # keep watching, alert on new trades
```

`--once` is the quickest way to check that everything works. It prints the most recent matching trades and never sends Telegram or Discord messages.

## Command line

| Option | What it does |
|---|---|
| `--once` | Fetch once, print the most recent matching trades (20 by default), exit. Sends nothing to Telegram/Discord. Exit code 0 on success, 1 if the API could not be reached, 2 if the API answered in an unexpected format. |
| `--min-size USD` | Minimum trade size. Overrides the config file and `MIN_TRADE_SIZE`. |
| `--limit N` | How many recent matching trades to fetch per poll (max 1000). Overrides `fetch_limit`. |
| `--export FILE` | Append every reported trade to `FILE`. The extension picks the format: `.csv` or `.json`. Works with and without `--once`. |
| `--config PATH` | Use another config file. Default is `config.yaml` in the current directory. |

Export columns: `timestamp` (UTC), `market`, `outcome`, `side`, `amount_usd`, `price`, `shares`, `wallet`, `trader`, `market_url`, `transaction_hash`.

## Configuration

Settings come from `config.yaml`. Environment variables override the file, and a `.env` file in the working directory is loaded automatically (copy `.env.example`). Everything is optional.

| `config.yaml` key | Environment variable | Default | Meaning |
|---|---|---|---|
| `min_trade_size` | `MIN_TRADE_SIZE` | `500` | Report trades of at least this many USD. |
| `check_interval` | `CHECK_INTERVAL` | `30` | Seconds between polls. |
| `fetch_limit` | none | `500` | Recent matching trades fetched per poll, 1 to 1000. |
| `telegram.bot_token` | `TELEGRAM_BOT_TOKEN` | empty | Telegram bot token. |
| `telegram.chat_id` | `TELEGRAM_CHAT_ID` | empty | Chat or channel that receives alerts. |
| `discord.webhook_url` | `DISCORD_WEBHOOK_URL` | empty | Discord webhook that receives alerts. |
| `polymarket.api_url` | `POLYMARKET_API_URL` | `https://data-api.polymarket.com/v2` | Base URL of the Polymarket Data API. |

`HTTPS_PROXY` is honoured by the HTTP library if you need to go through a proxy.

## How alerts work

1. Every `check_interval` seconds the script requests the newest trades of at least `min_trade_size` USD from `GET {api_url}/trades` (the size filter runs on Polymarket's side).
2. On the first poll it only remembers what is already there, so starting the script does not replay old trades.
3. On every later poll, each trade it has not seen before is printed, exported if `--export` is set, and sent to Telegram and Discord if they are configured.
4. If a poll fails (network error, HTTP error, unexpected format) it logs the reason and tries again on the next cycle. A failed poll does not cause duplicate alerts.

Telegram setup: create a bot with [@BotFather](https://t.me/BotFather), put its token in `TELEGRAM_BOT_TOKEN`, and put the target chat ID in `TELEGRAM_CHAT_ID` (for a channel, add the bot as an admin). Discord setup: create a webhook in the channel settings and put its URL in `DISCORD_WEBHOOK_URL`. Without either, the script runs in terminal-only mode. Messages are sent as plain text.

## Daily summary (optional)

`whale_of_day.py` posts one message with the active market that had the highest 24-hour volume (ignoring markets already priced below 0.02 or above 0.98). Run it from cron once a day.

```bash
python whale_of_day.py --dry-run    # print the message, send nothing
python whale_of_day.py              # needs TELEGRAM_BOT_TOKEN and WHALE_CHANNEL_ID (or TELEGRAM_CHAT_ID)
```

## Tests

```bash
python -m unittest discover -s tests -v    # offline, no network
python main.py --once                      # live check against the real API
```

## Limits and known gaps

- Not real time. Polymarket serves this endpoint through a cache that holds each response for up to 5 minutes (`Cache-Control: max-age=300`, measured 16 November 2026), so an alert can arrive several minutes after the trade. Polling more often than every 30 seconds does not help.
- "Whale" only means a single fill above your threshold. A large order that is split into many small fills is not detected, and nothing here measures whether following large trades is profitable.
- Many large trades are at prices like 0.999 on markets that are effectively decided. They are reported like any other trade; there is no price filter yet.
- If more matching trades happen between two polls than `fetch_limit`, the oldest ones are missed. The script logs a warning when that is likely.
- State is in memory only. After a restart the script starts fresh and does not report trades that happened while it was down.
- Open feature requests, not implemented: filter by market category (#2), wallet tagging (#3), per-market cooldown (#4), web dashboard (#6).
- Polymarket restricts trading in some countries. This tool only reads public data; whether the Data API is reachable from your network is outside its control.
- The Polymarket API is not versioned for third parties and has changed before (the CLOB `/trades` endpoint used by version 1 of this tool now requires an API key, and Data API v1 was retired). If the format changes again the script stops reporting and logs a "Polymarket API format problem" error instead of silently showing nothing.

## Status

This tracker is maintained. The hosted PolyClawster trading service that earlier versions of this README pointed to was closed in October 2026; its relay URL (`polyclawster.com/api/clob-relay`) no longer works, and the script ignores it if it is still in your config.

Issues and pull requests are welcome.

## License

MIT. Built by [Virix Labs](https://virixlabs.com).
