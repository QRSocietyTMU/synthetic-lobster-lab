"""Boundary and replay checks for the inclusive pre-execution spread limit."""

from collections import Counter
from copy import deepcopy
import unittest

from generate_lobster import Config, simulate, spread_allows_trade, validate_result


class SpreadThresholdTests(unittest.TestCase):
    def test_exact_dollar_boundaries_use_integer_price_units(self):
        # A $3.00 bid / $3.01 ask requires at least a one-cent allowance.
        for threshold, expected in ((0, False), (0.0099, False), (0.01, True)):
            with self.subTest(threshold=threshold):
                config = Config(max_trade_spread=threshold)
                self.assertEqual(spread_allows_trade(30000, 30100,
                                                    round(config.max_trade_spread * 10000)),
                                 expected)
        self.assertFalse(spread_allows_trade(30000, 40000, 9900))
        self.assertTrue(spread_allows_trade(30000, 40000, 10000))
        self.assertFalse(spread_allows_trade(30000, 30000, 10000))
        self.assertFalse(spread_allows_trade(30100, 30000, 10000))
        self.assertEqual(Config().max_trade_spread, 1.00)

    def test_threshold_configuration_rejects_invalid_values(self):
        for threshold in (-0.01, 10.01, float("nan"), float("inf"), True, 0.00001, 0.01001):
            with self.subTest(threshold=threshold):
                with self.assertRaisesRegex(ValueError, "max_trade_spread"):
                    Config(max_trade_spread=threshold)
        for threshold in (0, 0.0001, 0.0099, 10):
            self.assertEqual(Config(max_trade_spread=threshold).max_trade_spread, threshold)

    def assert_execution_and_replacement_prices(self, result):
        """Inspect the book immediately before each message, including fills
        sharing one incoming action's timestamp.
        """
        initial = result["initial_orders"]
        bid = max(o["price"] for o in initial if o["side"] == 1)
        ask = min(o["price"] for o in initial if o["side"] == -1)
        maximum = round(result["config"]["max_trade_spread"] * 10000)
        blocked_count = 0
        stopped_after_partial_fill = False
        previous_message = None
        for index, (message, book, event) in enumerate(zip(result["messages"],
                                                          result["orderbook"], result["events"])):
            _, kind, _, _, price, side = message
            with self.subTest(event=index):
                if kind == 4:
                    self.assertGreater(ask - bid, 0)
                    self.assertLessEqual(ask - bid, maximum)
                    self.assertEqual(price, bid if side == 1 else ask)
                blocked_increment = event["spread_blocked_orders"] - blocked_count
                self.assertIn(blocked_increment, (0, 1))
                if blocked_increment:
                    self.assertEqual(kind, 1)
                    self.assertEqual(event["resting_owner"], "background")
                    self.assertGreater(ask - bid, maximum)
                    self.assertEqual(price, bid if side == 1 else ask)
                    if (previous_message and previous_message[1] == 4
                            and previous_message[0] == message[0]):
                        stopped_after_partial_fill = True
                blocked_count = event["spread_blocked_orders"]
                self.assertLess(book[2], book[0])
            bid, ask = book[2], book[0]
            previous_message = message
        self.assertEqual(blocked_count, result["summary"]["spread_blocked_orders"])
        self.assertTrue(validate_result(result)["passed"])
        return stopped_after_partial_fill

    def test_zero_threshold_blocks_all_executions_and_fees(self):
        result = simulate(Config(num_events=1000, seed=42, max_trade_spread=0, fee_bps=100))
        self.assert_execution_and_replacement_prices(result)
        self.assertFalse(any(message[1] == 4 for message in result["messages"]))
        self.assertEqual(result["candles"], [])
        for key in ("trade_count", "volume", "maker_fills", "maker_fees_paid", "exchange_fees"):
            self.assertEqual(result["summary"][key], 0, key)
        self.assertGreater(result["summary"]["spread_blocked_orders"], 0)
        self.assertEqual(result["summary"]["inventory"], result["config"]["initial_inventory"])
        self.assertEqual(result["summary"]["cash"], result["config"]["initial_cash"])

    def test_narrow_threshold_is_rechecked_between_fills(self):
        saw_multiple_fills = saw_partial_then_blocked = False
        for threshold in (0.01, 0.03):
            with self.subTest(threshold=threshold):
                result = simulate(Config(num_events=1000, seed=42, max_trade_spread=threshold))
                saw_partial_then_blocked |= self.assert_execution_and_replacement_prices(result)
                trades_per_action = Counter(message[0] for message in result["messages"] if message[1] == 4)
                self.assertTrue(trades_per_action)
                self.assertGreater(result["summary"]["spread_blocked_orders"], 0)
                saw_multiple_fills |= max(trades_per_action.values()) > 1
        self.assertTrue(saw_multiple_fills, "The fixture must exercise more than one fill per incoming action")
        self.assertTrue(saw_partial_then_blocked,
                        "The fixture must exercise a fill that widens the spread and blocks the remainder")

    def test_replay_rejects_execution_when_metadata_threshold_is_lowered(self):
        result = simulate(Config(num_events=150, seed=42, max_trade_spread=1))
        self.assertGreater(result["summary"]["trade_count"], 0)
        changed = deepcopy(result)
        changed["config"]["max_trade_spread"] = 0
        validation = validate_result(changed)
        self.assertFalse(validation["passed"])
        self.assertIn("execution exceeds spread threshold", "; ".join(validation["errors"]))


if __name__ == "__main__":
    unittest.main()
