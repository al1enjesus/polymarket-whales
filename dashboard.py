#!/usr/bin/env python3
"""
🐋 Polymarket Whale Tracker — Streamlit dashboard
Live feed of whale trades with size/market filters, no Telegram/Discord setup required.

Run with: streamlit run dashboard.py
"""
import time
from datetime import datetime, timezone

import streamlit as st

from main import (
    load_config,
    fetch_recent_trades,
    parse_trade_usd_size,
    format_side,
    get_market_title,
)

st.set_page_config(page_title="Polymarket Whale Tracker", page_icon="🐋", layout="wide")

config = load_config()

st.sidebar.header("Filters")
min_size = st.sidebar.slider(
    "Min trade size (USD)",
    min_value=0,
    max_value=50_000,
    value=int(config["min_trade_size"]),
    step=100,
)
market_filter = st.sidebar.text_input("Market contains", "")
refresh_seconds = st.sidebar.slider(
    "Refresh interval (s)", min_value=5, max_value=60, value=int(config["check_interval"])
)

st.title("🐋 Polymarket Whale Tracker")
st.caption(f"Showing trades ≥ ${min_size:,} · refreshing every {refresh_seconds}s")

if "whale_trades" not in st.session_state:
    st.session_state.whale_trades = []
if "seen_ids" not in st.session_state:
    st.session_state.seen_ids = set()

api_url = config["polymarket"]["api_url"]
trades = fetch_recent_trades(api_url)

for trade in trades:
    trade_id = trade.get("id") or trade.get("trade_id") or str(trade)
    if trade_id in st.session_state.seen_ids:
        continue
    st.session_state.seen_ids.add(trade_id)

    amount_usd = parse_trade_usd_size(trade)
    if amount_usd < min_size:
        continue

    condition_id = trade.get("market") or trade.get("condition_id", "")
    market_title = get_market_title(condition_id) if condition_id else "Unknown Market"
    side = format_side(trade.get("side", trade.get("outcome", "")))
    price = float(trade.get("price", 0))

    ts_raw = trade.get("timestamp") or trade.get("created_at", "")
    if isinstance(ts_raw, (int, float)):
        ts = datetime.fromtimestamp(ts_raw, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    elif ts_raw:
        ts = str(ts_raw)[:19].replace("T", " ")
    else:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    st.session_state.whale_trades.insert(
        0,
        {
            "Time (UTC)": ts,
            "Market": market_title,
            "Side": side,
            "Amount (USD)": amount_usd,
            "Price": price,
        },
    )

# Keep the feed bounded so the dashboard doesn't grow unbounded in a long session.
st.session_state.whale_trades = st.session_state.whale_trades[:200]

filtered = [
    t for t in st.session_state.whale_trades if market_filter.lower() in t["Market"].lower()
]

if filtered:
    st.dataframe(
        filtered,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Amount (USD)": st.column_config.NumberColumn(format="$%.2f"),
            "Price": st.column_config.NumberColumn(format="%.4f"),
        },
    )
else:
    st.info("No whale trades matching filters yet. Waiting for the next check...")

time.sleep(refresh_seconds)
st.rerun()
