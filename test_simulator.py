"""Independent checks of the public simulator API and exported market data.

Run with: python -m unittest -v test_simulator.py
The replay deliberately rebuilds depth without using the simulator's validator.
"""

import csv
from copy import deepcopy
import json
import math
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from zipfile import ZipFile

from generate_lobster import Config, export_result, simulate, validate_result


ASK_EMPTY = 9_999_999_999
BID_EMPTY = -9_999_999_999
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def replay_snapshot(orders, depth=10):
    """Aggregate all live orders and encode the requested occupied levels."""
    levels = {1: defaultdict(int), -1: defaultdict(int)}
    for order in orders.values():
        levels[order["side"]][order["price"]] += order["size"]
    asks = sorted(levels[-1].items())
    bids = sorted(levels[1].items(), reverse=True)
    row = []
    for index in range(depth):
        row.extend(asks[index] if index < len(asks) else (ASK_EMPTY, 0))
        row.extend(bids[index] if index < len(bids) else (BID_EMPTY, 0))
    return row


class SimulatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = simulate(Config(num_events=600, seed=42))

    def assert_replay(self, result, expected_count):
        config = result["config"]
        messages = result["messages"]
        books = result["orderbook"]
        self.assertEqual(len(messages), expected_count)
        self.assertEqual(len(books), expected_count)
        self.assertEqual(len(result["events"]), expected_count)
        tick = round(config["tick_size"] * 10_000)
        orders = {}
        all_ids = set()
        arrival = 0
        for original in result["initial_orders"]:
            order = dict(original)
            self.assertNotIn(order["id"], all_ids)
            self.assertIn(order["side"], (-1, 1))
            self.assertGreater(order["size"], 0)
            self.assertGreater(order["price"], 0)
            self.assertEqual(order["price"] % tick, 0)
            self.assertIn(order["owner"], ("maker", "background"))
            order["arrival"] = arrival
            arrival += 1
            orders[order["id"]] = order
            all_ids.add(order["id"])

        previous_time = config["start_time"]
        last_trade = config["initial_price"]
        for index, (message, actual_book, event) in enumerate(
            zip(messages, books, result["events"])
        ):
            with self.subTest(event=index):
                self.assertEqual(len(message), 6)
                self.assertEqual(len(actual_book), 40)
                time, kind, order_id, size, price, side = message
                self.assertTrue(math.isfinite(time))
                self.assertGreaterEqual(time, previous_time)
                self.assertLessEqual(
                    time, config["start_time"] + config["duration_seconds"]
                )
                previous_time = time
                self.assertIn(kind, (1, 2, 3, 4))
                self.assertIn(side, (-1, 1))
                for value in (kind, order_id, size, price, side):
                    self.assertEqual(value, int(value))
                self.assertGreater(size, 0)
                self.assertGreater(price, 0)
                self.assertEqual(price % tick, 0)
                if kind == 1:
                    self.assertNotIn(order_id, all_ids)
                    all_ids.add(order_id)
                    orders[order_id] = dict(
                        id=order_id, side=side, price=price,
                        size=size, arrival=arrival,
                    )
                    arrival += 1
                else:
                    self.assertIn(order_id, orders)
                    order = orders[order_id]
                    self.assertEqual((price, side), (order["price"], order["side"]))
                    self.assertLessEqual(size, order["size"])
                    if kind == 2:
                        self.assertLess(size, order["size"])
                    elif kind == 3:
                        self.assertEqual(size, order["size"])
                    elif kind == 4:
                        # A visible execution must take the best resting price,
                        # then the oldest resting order at that price.
                        same_side = [o for o in orders.values() if o["side"] == side]
                        best = min(same_side, key=lambda o: (-side * o["price"], o["arrival"]))
                        self.assertEqual(order_id, best["id"])
                        last_trade = price / 10_000
                    order["size"] -= size
                    if order["size"] == 0:
                        del orders[order_id]

                self.assertEqual(list(actual_book), replay_snapshot(orders))
                self.assertTrue(all(value == int(value) for value in actual_book))
                self.assertLess(actual_book[2], actual_book[0])
                self.assertGreater(actual_book[1], 0)
                self.assertGreater(actual_book[3], 0)
                self.assertAlmostEqual(event["time"], time, places=9)
                self.assertEqual((event["type"], event["size"], event["side"]), (kind, size, side))
                self.assertAlmostEqual(event["price"], price / 10_000, places=9)
                self.assertAlmostEqual(event["last_price"], last_trade, places=9)
                self.assertAlmostEqual(event["best_ask"], actual_book[0] / 10_000, places=9)
                self.assertAlmostEqual(event["best_bid"], actual_book[2] / 10_000, places=9)

    def test_replay_and_post_event_snapshots(self):
        self.assert_replay(self.result, 600)
        validation = validate_result(self.result)
        self.assertTrue(validation["passed"], validation)
        self.assertTrue(validation["checks"])
        self.assertTrue(self.result["validation"]["passed"])

    def corrupted_results(self):
        bad_snapshot = deepcopy(self.result)
        bad_snapshot["orderbook"][0][1] += 1
        overfill = deepcopy(self.result)
        remaining = {order["id"]: order["size"] for order in overfill["initial_orders"]}
        for message in overfill["messages"]:
            _, kind, order_id, size, _, _ = message
            if kind == 1:
                remaining[order_id] = size
            elif kind == 4:
                message[3] = remaining[order_id] + 1
                break
            else:
                remaining[order_id] -= size
        else:
            self.fail("The fixed scenario must contain an execution to corrupt")
        return [(bad_snapshot, "snapshot replay mismatch"), (overfill, "over-removal")]

    def test_validation_and_export_reject_corrupt_data(self):
        for result, expected_error in self.corrupted_results():
            with self.subTest(error=expected_error), tempfile.TemporaryDirectory() as directory:
                validation = validate_result(result)
                self.assertFalse(validation["passed"], validation)
                self.assertIn(expected_error, "; ".join(validation["errors"]))
                with self.assertRaisesRegex(ValueError, "Refusing invalid export"):
                    export_result(result, directory, include_xlsx=False)
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_optimized_python_rejects_corrupt_data(self):
        # Explicit exceptions in the child are intentional: Python -O removes
        # assert statements, so assertions cannot verify this regression.
        script = """
import json
import sys
from generate_lobster import validate_result
with open(sys.argv[1], encoding="utf-8") as source:
    cases = json.load(source)
for result, expected_error in cases:
    validation = validate_result(result)
    if validation["passed"] or expected_error not in "; ".join(validation["errors"]):
        raise RuntimeError(f"Optimized validation accepted corruption: {validation}")
"""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "corrupted.json"
            source.write_text(json.dumps(self.corrupted_results()), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "-O", "-c", script, str(source)],
                cwd=Path(__file__).resolve().parent, capture_output=True, text=True,
                timeout=30,
            )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_high_initial_price_must_align_with_tick(self):
        self.assertEqual(Config(initial_price=100000.0).initial_price, 100000.0)
        with self.assertRaisesRegex(ValueError, "initial_price must align with tick_size"):
            Config(initial_price=99999.99999, tick_size=0.01)

    def test_seed_reproducibility(self):
        repeated = simulate(Config(num_events=600, seed=42))
        for key in ("initial_orders", "messages", "orderbook", "events", "candles", "summary"):
            self.assertEqual(self.result[key], repeated[key], key)
        changed = simulate(Config(num_events=600, seed=43))
        self.assertNotEqual(self.result["messages"], changed["messages"])

    def test_candles_use_only_visible_executions(self):
        result = self.result
        config = result["config"]
        buckets = {}
        for time, kind, _, size, price, _ in result["messages"]:
            if kind != 4:
                continue
            bucket = config["start_time"] + math.floor(
                (time - config["start_time"]) / config["candle_seconds"]
            ) * config["candle_seconds"]
            dollars = price / 10_000
            if bucket not in buckets:
                buckets[bucket] = dict(
                    time=bucket, open=dollars, high=dollars, low=dollars,
                    close=dollars, volume=0,
                )
            candle = buckets[bucket]
            candle["high"] = max(candle["high"], dollars)
            candle["low"] = min(candle["low"], dollars)
            candle["close"] = dollars
            candle["volume"] += size
        self.assertTrue(buckets, "The fixed teaching scenario should contain trades")
        expected = list(buckets.values())
        self.assertEqual(len(expected), len(result["candles"]))
        for actual, wanted in zip(result["candles"], expected):
            for key, value in wanted.items():
                self.assertAlmostEqual(actual[key], value, places=8, msg=key)

    def test_accounting_and_exchange_fees(self):
        result = self.result
        config = result["config"]
        initial_equity = config["initial_cash"] + config["initial_inventory"] * config["initial_price"]
        previous_cash = config["initial_cash"]
        previous_inventory = config["initial_inventory"]
        previous_maker_fills = 0
        total_fees = 0.0
        volume = 0
        trades = 0
        for index, (message, event) in enumerate(zip(result["messages"], result["events"])):
            with self.subTest(event=index):
                _, kind, order_id, size, price, side = message
                trade_fee = size * (price / 10_000) * config["fee_bps"] / 10_000 if kind == 4 else 0.0
                if kind == 4:
                    trades += 1
                    volume += size
                    total_fees += trade_fee
                self.assertAlmostEqual(event["exchange_fees"], total_fees, places=7)
                self.assertEqual(event["volume"], volume)
                self.assertEqual(event["trade_count"], trades)
                maker_change = event["maker_fills"] - previous_maker_fills
                expected_maker_fill = int(
                    kind == 4 and result["order_owners"][str(order_id)] == "maker"
                )
                self.assertEqual(maker_change, expected_maker_fill)
                inventory_change = event["inventory"] - previous_inventory
                cash_change = event["cash"] - previous_cash
                if maker_change:
                    self.assertEqual(kind, 4)
                    self.assertEqual(inventory_change, side * size)
                    self.assertAlmostEqual(
                        cash_change, -inventory_change * (price / 10_000) - trade_fee / 2,
                        places=7,
                    )
                else:
                    self.assertEqual(inventory_change, 0)
                    self.assertAlmostEqual(cash_change, 0, places=7)
                midpoint = (event["best_bid"] + event["best_ask"]) / 2
                self.assertAlmostEqual(event["equity"], event["cash"] + event["inventory"] * midpoint, places=7)
                self.assertAlmostEqual(event["pnl"], event["equity"] - initial_equity, places=7)
                previous_cash = event["cash"]
                previous_inventory = event["inventory"]
                previous_maker_fills = event["maker_fills"]
        self.assertGreater(trades, 0)
        self.assertGreater(previous_maker_fills, 0)
        final = result["events"][-1]
        for key in ("last_price", "best_bid", "best_ask", "pnl", "equity", "cash", "inventory", "exchange_fees", "volume", "trade_count", "maker_fills"):
            self.assertAlmostEqual(result["summary"][key], final[key], places=7, msg=key)
        self.assertEqual(result["summary"]["event_count"], len(result["messages"]))

    def test_small_event_budgets(self):
        for count in (1, 2, 3, 7):
            with self.subTest(count=count):
                result = simulate(Config(num_events=count, seed=5))
                self.assert_replay(result, count)
                self.assertTrue(validate_result(result)["passed"])

    def test_extreme_valid_inputs(self):
        settings = [
            dict(buyer_drift=0.02, seller_drift=-0.02, buyer_volatility=0.02, seller_volatility=0.02),
            dict(buyer_drift=-0.02, seller_drift=0.02, buyer_volatility=0.02, seller_volatility=0.02),
            dict(buyer_drift=0.02, seller_drift=0.02, buyer_volatility=0.0, seller_volatility=0.0),
            dict(buyer_drift=-0.02, seller_drift=-0.02, buyer_volatility=0.0, seller_volatility=0.0),
        ]
        for settings_for_case in settings:
            with self.subTest(settings=settings_for_case):
                result = simulate(Config(num_events=150, seed=17, **settings_for_case))
                self.assert_replay(result, 150)
                self.assertTrue(validate_result(result)["passed"])
                for event in result["events"]:
                    for key in ("buyer_value", "seller_value", "cash", "equity", "pnl"):
                        self.assertTrue(math.isfinite(event[key]), key)

    def test_zero_volatility_and_no_cancellations(self):
        result = simulate(Config(
            num_events=150, seed=2, buyer_drift=0, seller_drift=0,
            buyer_volatility=0, seller_volatility=0, cancellation_probability=0,
        ))
        self.assert_replay(result, 150)
        # Zero controls background cancellations. Maker quote refreshes may
        # still explicitly delete owned orders before replacing them.
        for row in result["messages"]:
            if row[1] in (2, 3):
                self.assertEqual(result["order_owners"][str(row[2])], "maker")
        for event in result["events"]:
            self.assertAlmostEqual(event["buyer_value"], result["config"]["initial_price"], places=8)

    def test_deterministic_drift_uses_minutes(self):
        for drift in (-0.02, 0.02):
            with self.subTest(buyer_drift=drift):
                result = simulate(Config(
                    num_events=90, seed=23, buyer_drift=drift, seller_drift=-drift,
                    buyer_volatility=0, seller_volatility=0, seller_mean_reversion=0,
                ))
                config = result["config"]
                for event in result["events"]:
                    minutes = (event["time"] - config["start_time"]) / 60
                    expected_buyer = config["initial_price"] * math.exp(drift * minutes)
                    expected_seller = config["initial_price"] * (1 - drift * minutes)
                    self.assertAlmostEqual(event["buyer_value"], expected_buyer, delta=1e-7)
                    self.assertAlmostEqual(event["seller_value"], expected_seller, delta=1e-7)

    def test_exports_are_numeric_headerless_and_match(self):
        result = simulate(Config(num_events=35, seed=9))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            export_result(result, directory, include_xlsx=True)
            tables = {}
            for sheet, key, width in (("message", "messages", 6), ("orderbook", "orderbook", 40)):
                path = directory / f"SYNTH_{sheet}_10.csv"
                self.assertTrue(path.is_file(), str(path))
                with path.open(newline="") as source:
                    table = [[float(cell) for cell in row] for row in csv.reader(source)]
                self.assertEqual(len(table), len(result[key]))
                for actual, expected in zip(table, result[key]):
                    self.assertEqual(len(actual), width)
                    for a, e in zip(actual, expected):
                        self.assertAlmostEqual(a, e, delta=1e-9)
                tables[sheet] = table
            workbook_path = directory / "SYNTH_market_data.xlsx"
            self.assertTrue(workbook_path.is_file())
            with ZipFile(workbook_path) as workbook:
                root = ET.fromstring(workbook.read("xl/workbook.xml"))
                sheets = root.findall("s:sheets/s:sheet", NS)
                self.assertEqual([sheet.attrib["name"] for sheet in sheets], ["message", "orderbook"])
                relationships = ET.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
                targets = {item.attrib["Id"]: item.attrib["Target"] for item in relationships}
                rel_key = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
                for sheet in sheets:
                    target = targets[sheet.attrib[rel_key]]
                    part = target.lstrip("/") if target.startswith("/") else "xl/" + target
                    xml = ET.fromstring(workbook.read(part))
                    rows = xml.findall("s:sheetData/s:row", NS)
                    expected_rows = tables[sheet.attrib["name"]]
                    self.assertEqual(len(rows), len(expected_rows))
                    for row, expected in zip(rows, expected_rows):
                        cells = row.findall("s:c", NS)
                        self.assertEqual(len(cells), len(expected))
                        for cell, value in zip(cells, expected):
                            self.assertIn(cell.get("t", "n"), ("n",))
                            number = cell.find("s:v", NS)
                            self.assertIsNotNone(number)
                            self.assertEqual(float(number.text), value)


if __name__ == "__main__":
    unittest.main()
