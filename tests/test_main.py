"""
Offline tests (no network). Run with:  python -m unittest discover -s tests -v
For a live check against the real API use:  python main.py --once
"""
import csv
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main  # noqa: E402

# One trade exactly as returned by GET {api}/trades on 2026-11-16.
TRADE = {
    "proxy_wallet": "0x7b7e9a71189008f01edca545bd61d89132f9ef88",
    "side": "BUY",
    "token_id": "44800176128841389415380336676252326143341709812940397385230845189588540893165",
    "condition_id": "0x662e9fc027f3627c76d8038e7404824b502b6ebd4814eec21d94937716265be4",
    "size": 3495.307334,
    "price": 0.1430489374,
    "timestamp": 1794808967,
    "title": "Kobe: Ryuki Matsuda vs Fajing Sun",
    "slug": "atp-matsud-su-2026-11-16",
    "event_slug": "atp-matsud-su-2026-11-16",
    "outcome": "Ryuki Matsuda",
    "name": "some_trader",
    "pseudonym": "Fuzzy-Example",
    "transaction_hash": "0xc087fdf7fb0ac8e6458ce51726a25d6bbc9bda52a673ef0812c41367a6202bcb",
}


def fake_response(payload, status=200):
    resp = mock.Mock()
    resp.status_code = status
    resp.headers = {}
    resp.json.return_value = payload
    resp.text = json.dumps(payload)
    resp.raise_for_status.return_value = None
    return resp


class TradeMaths(unittest.TestCase):
    def test_usd_size_is_shares_times_price(self):
        self.assertAlmostEqual(main.parse_trade_usd_size(TRADE), 500.0, places=1)

    def test_bad_numbers_do_not_crash(self):
        self.assertEqual(main.parse_trade_usd_size({"size": "x", "price": None}), 0.0)

    def test_normalize_keeps_side_and_outcome_separate(self):
        t = main.normalize_trade(TRADE)
        self.assertEqual((t["side"], t["outcome"]), ("BUY", "Ryuki Matsuda"))
        self.assertEqual(t["timestamp"], "2026-11-16 06:02:47")
        self.assertEqual(
            t["market_url"],
            "https://polymarket.com/event/atp-matsud-su-2026-11-16/atp-matsud-su-2026-11-16",
        )
        self.assertIn("0x7b7e...ef88", main.trader_label(t))

    def test_fills_in_one_transaction_get_distinct_ids(self):
        other_fill = dict(TRADE, size=10.0)
        self.assertNotEqual(main.trade_unique_id(TRADE), main.trade_unique_id(other_fill))
        self.assertEqual(main.trade_unique_id(TRADE), main.trade_unique_id(dict(TRADE)))

    def test_plain_message_has_no_markdown(self):
        msg = main.format_plain_message(main.normalize_trade(dict(TRADE, title="A_b *c*")))
        self.assertIn("Market: A_b *c*", msg)
        self.assertIn("$500.00", msg)


class ApiShape(unittest.TestCase):
    def fetch(self, payload):
        with mock.patch.object(main.requests, "get", return_value=fake_response(payload)):
            return main.fetch_recent_trades("https://example.invalid", 500, 10)

    def test_good_response(self):
        self.assertEqual(self.fetch({"data": [TRADE], "pagination": {}}), [TRADE])

    def test_empty_is_fine(self):
        self.assertEqual(self.fetch({"data": []}), [])

    def test_missing_data_key_is_a_clear_error(self):
        with self.assertRaises(main.ApiShapeError) as ctx:
            self.fetch({"error": "Data API v2 has been retired"})
        self.assertIn("may have changed", str(ctx.exception))

    def test_renamed_field_is_a_clear_error(self):
        renamed = {k: v for k, v in TRADE.items() if k != "price"}
        with self.assertRaises(main.ApiShapeError) as ctx:
            self.fetch({"data": [renamed]})
        self.assertIn("price", str(ctx.exception))

    def test_once_returns_2_on_shape_error_and_sends_nothing(self):
        cfg = main.load_config("/nonexistent.yaml")
        cfg["telegram"].update(bot_token="t", chat_id="c")
        with mock.patch.object(main.requests, "get", return_value=fake_response([1, 2])), \
                mock.patch.object(main.requests, "post") as post:
            self.assertEqual(main.run(cfg, once=True), 2)
        post.assert_not_called()

    def test_once_prints_and_exports_without_sending(self):
        cfg = main.load_config("/nonexistent.yaml")
        cfg["telegram"].update(bot_token="t", chat_id="c")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "w.csv")
            with mock.patch.object(main.requests, "get",
                                   return_value=fake_response({"data": [TRADE]})), \
                    mock.patch.object(main.requests, "post") as post:
                self.assertEqual(main.run(cfg, export_path=path, once=True), 0)
            post.assert_not_called()
            with open(path, newline="") as f:
                rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["market"], TRADE["title"])


class Config(unittest.TestCase):
    def test_env_overrides(self):
        env = {"MIN_TRADE_SIZE": "2500", "CHECK_INTERVAL": "7", "DISCORD_WEBHOOK_URL": "https://d/x"}
        with mock.patch.dict(os.environ, env):
            cfg = main.load_config("/nonexistent.yaml")
        self.assertEqual(cfg["min_trade_size"], 2500.0)
        self.assertEqual(cfg["check_interval"], 7)
        self.assertEqual(cfg["discord"]["webhook_url"], "https://d/x")

    def test_old_clob_url_falls_back_to_data_api(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write('polymarket:\n  api_url: "https://clob.polymarket.com"\n')
        try:
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("POLYMARKET_API_URL", None)
                cfg = main.load_config(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(cfg["polymarket"]["api_url"], main.DEFAULT_API_URL)


class Export(unittest.TestCase):
    def test_json_appends(self):
        t = main.normalize_trade(TRADE)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "w.json")
            main.export_trade(path, t)
            main.export_trade(path, t)
            with open(path) as f:
                data = json.load(f)
        self.assertEqual(len(data), 2)
        self.assertEqual(list(data[0].keys()), main.EXPORT_FIELDS)


if __name__ == "__main__":
    unittest.main()
