"""Checks fee comparisons and the maker's marked P&L attribution."""

from fractions import Fraction
import unittest

from generate_lobster import Config, simulate


class CommissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = [simulate(Config(num_events=600, seed=42, fee_bps=rate))
                    for rate in (0.0, 7.5, 100.0)]

    def test_commission_preserves_market_path_and_gross_pnl(self):
        no_fees = self.runs[0]
        for result in self.runs[1:]:
            with self.subTest(fee_bps=result["config"]["fee_bps"]):
                for key in ("initial_orders", "order_owners", "messages", "orderbook", "candles"):
                    self.assertEqual(result[key], no_fees[key], key)
                for charged, free in zip(result["events"], no_fees["events"]):
                    for key in ("inventory", "maker_fills", "volume", "trade_count",
                                "maker_trade_edge", "maker_inventory_revaluation"):
                        self.assertEqual(charged[key], free[key], key)
                    self.assertAlmostEqual(charged["pnl_before_fees"], free["pnl"], delta=1e-7)
                    self.assertAlmostEqual(charged["cash"] + charged["maker_fees_paid"],
                                           free["cash"], delta=1e-7)

    def test_fees_match_executed_notional_and_only_charge_maker_fills(self):
        for result in self.runs:
            rate = Fraction(str(result["config"]["fee_bps"])) / 10000
            venue_fees = maker_fees = Fraction(0)
            previous_maker_fees = previous_venue_fees = 0.0
            maker_fill_count = other_fill_count = 0
            for message, event in zip(result["messages"], result["events"]):
                _, kind, order_id, size, price, _ = message
                is_maker_fill = kind == 4 and result["order_owners"][str(order_id)] == "maker"
                if kind == 4:
                    fee = size * Fraction(price, 10000) * rate
                    venue_fees += fee
                    if is_maker_fill:
                        maker_fill_count += 1
                        maker_fees += fee / 2
                    else:
                        other_fill_count += 1
                self.assertAlmostEqual(event["exchange_fees"], float(venue_fees), delta=1e-7)
                self.assertAlmostEqual(event["maker_fees_paid"], float(maker_fees), delta=1e-7)
                maker_increment = event["maker_fees_paid"] - previous_maker_fees
                if is_maker_fill:
                    self.assertAlmostEqual(event["exchange_fees"] - previous_venue_fees,
                                           2 * maker_increment, delta=1e-7)
                else:
                    self.assertEqual(maker_increment, 0)
                self.assertAlmostEqual(event["pnl"],
                                       event["pnl_before_fees"] - event["maker_fees_paid"],
                                       delta=1e-7)
                previous_maker_fees = event["maker_fees_paid"]
                previous_venue_fees = event["exchange_fees"]
            self.assertGreater(maker_fill_count, 0)
            self.assertGreater(other_fill_count, 0)
        for event in self.runs[0]["events"]:
            self.assertEqual(event["exchange_fees"], 0)
            self.assertEqual(event["maker_fees_paid"], 0)
            self.assertEqual(event["pnl"], event["pnl_before_fees"])

    def test_trade_edge_and_revaluation_reconcile_at_every_event(self):
        result = self.runs[1]
        config = result["config"]
        initial_mid = Fraction(str(config["initial_price"]))
        previous_mid = initial_mid
        inventory = config["initial_inventory"]
        gross_cash_change = edge = revaluation = Fraction(0)
        for message, book, event in zip(result["messages"], result["orderbook"], result["events"]):
            _, kind, order_id, size, price, side = message
            if kind == 4 and result["order_owners"][str(order_id)] == "maker":
                signed_quantity = side * size
                fill_price = Fraction(price, 10000)
                inventory += signed_quantity
                gross_cash_change -= signed_quantity * fill_price
                edge += signed_quantity * (previous_mid - fill_price)
            midpoint = Fraction(book[0] + book[2], 20000)
            revaluation += inventory * (midpoint - previous_mid)
            gross_pnl = gross_cash_change + inventory * midpoint - config["initial_inventory"] * initial_mid
            self.assertEqual(edge + revaluation, gross_pnl)
            self.assertAlmostEqual(event["maker_trade_edge"], float(edge), delta=1e-7)
            self.assertAlmostEqual(event["maker_inventory_revaluation"], float(revaluation), delta=1e-7)
            self.assertAlmostEqual(event["pnl_before_fees"], float(gross_pnl), delta=1e-7)
            previous_mid = midpoint
        for key in ("pnl", "pnl_before_fees", "maker_fees_paid", "maker_trade_edge",
                    "maker_inventory_revaluation"):
            self.assertEqual(result["summary"][key], result["events"][-1][key], key)

    def test_hand_calculated_fill_and_inventory_mark(self):
        result = simulate(Config(num_events=30, initial_price=100, initial_inventory=0,
                                 seed=42, fee_bps=10))
        fill, next_trade = result["events"][10:12]
        # Buy 100 at 99.97 while the midpoint is 100: $3 marked trade edge.
        self.assertEqual((fill["type"], fill["resting_owner"], fill["size"], fill["price"]),
                         (4, "maker", 100, 99.97))
        self.assertAlmostEqual(fill["maker_trade_edge"], 3, delta=1e-7)
        self.assertAlmostEqual(fill["maker_inventory_revaluation"], 0, delta=1e-7)
        self.assertAlmostEqual(fill["pnl_before_fees"], 3, delta=1e-7)
        self.assertAlmostEqual(fill["maker_fees_paid"], 4.9985, delta=1e-7)
        self.assertAlmostEqual(fill["pnl"], -1.9985, delta=1e-7)
        # Background execution lowers the bid by a cent. Holding 100 shares
        # through the half-cent midpoint drop loses $0.50, with no maker fee.
        self.assertEqual(next_trade["resting_owner"], "background")
        self.assertAlmostEqual(next_trade["maker_trade_edge"], 3, delta=1e-7)
        self.assertAlmostEqual(next_trade["maker_inventory_revaluation"], -0.5, delta=1e-7)
        self.assertAlmostEqual(next_trade["pnl_before_fees"], 2.5, delta=1e-7)
        self.assertAlmostEqual(next_trade["pnl"], -2.4985, delta=1e-7)


if __name__ == "__main__":
    unittest.main()
