#!/usr/bin/env python3
"""A small educational exchange: agent valuations drive a real FIFO order book.

Prices inside the book are integer USD x 10,000. Time is seconds after
midnight; the stochastic equations use MINUTES. The output is synthetic.
"""
from __future__ import annotations

# ===== STUDENT CONFIGURATION =====
INITIAL_PRICE = 223.82
BUYER_DRIFT = 0.0              # fractional return / minute; may be negative
BUYER_VOLATILITY = 0.006       # fractional scale / sqrt(minute); nonnegative
SELLER_DRIFT = 0.0             # fractional initial-price drift / minute
SELLER_VOLATILITY = 0.006      # fractional initial-price scale / sqrt(minute)
SELLER_MEAN_REVERSION = 1.5    # pull toward last trade, per minute
NUM_EVENTS = 3000
NUM_LEVELS = 10                # this prototype exports the 10-level schema
DURATION_SECONDS = 300.0
TICK_SIZE = 0.01
RANDOM_SEED = 42
BUYER_PROBABILITY = 0.50
CANCELLATION_PROBABILITY = 0.15
MAX_TRADE_SPREAD = 1.00        # USD; allow aggression when spread <= this value
START_TIME = 34200.0           # 09:30:00, seconds after midnight
# =================================

import argparse
import csv
from collections import OrderedDict
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import warnings

import numpy as np


CONFIG_LIMITS = {
    "initial_price": {"min": 1.0, "max": 100000.0, "step": 0.01},
    "buyer_drift": {"min": -0.02, "max": 0.02, "step": 0.0005},
    "buyer_volatility": {"min": 0.0, "max": 0.05, "step": 0.0005},
    "seller_drift": {"min": -0.02, "max": 0.02, "step": 0.0005},
    "seller_volatility": {"min": 0.0, "max": 0.05, "step": 0.0005},
    "seller_mean_reversion": {"min": 0.0, "max": 10.0, "step": 0.1},
    "num_events": {"min": 1, "max": 20000, "step": 1},
    "num_levels": {"min": 10, "max": 10, "step": 1},
    "duration_seconds": {"min": 1.0, "max": 1800.0, "step": 1.0},
    "tick_size": {"min": 0.0001, "max": 1.0, "step": 0.0001},
    "seed": {"min": 0, "max": 4294967295, "step": 1},
    "buyer_probability": {"min": 0.0, "max": 1.0, "step": 0.01},
    "cancellation_probability": {"min": 0.0, "max": 1.0, "step": 0.01},
    "maker_half_spread_ticks": {"min": 1, "max": 20, "step": 1},
    "maker_order_size": {"min": 1, "max": 10000, "step": 1},
    "initial_cash": {"min": 0.0, "max": 1000000000.0, "step": 100.0},
    "initial_inventory": {"min": -1000000, "max": 1000000, "step": 1},
    "fee_bps": {"min": 0.0, "max": 100.0, "step": 0.1},
    "max_trade_spread": {"min": 0.0, "max": 10.0, "step": 0.01},
    "candle_seconds": {"min": 1, "max": 300, "step": 1},
    "start_time": {"min": 0.0, "max": 86399.0, "step": 1.0},
}


@dataclass
class Config:
    initial_price: float = INITIAL_PRICE
    buyer_drift: float = BUYER_DRIFT
    buyer_volatility: float = BUYER_VOLATILITY
    seller_drift: float = SELLER_DRIFT
    seller_volatility: float = SELLER_VOLATILITY
    seller_mean_reversion: float = SELLER_MEAN_REVERSION
    num_events: int = NUM_EVENTS
    num_levels: int = NUM_LEVELS
    duration_seconds: float = DURATION_SECONDS
    tick_size: float = TICK_SIZE
    seed: int = RANDOM_SEED
    buyer_probability: float = BUYER_PROBABILITY
    cancellation_probability: float = CANCELLATION_PROBABILITY
    maker_half_spread_ticks: int = 3
    maker_order_size: int = 100
    initial_cash: float = 1000000.0
    initial_inventory: int = 1000
    fee_bps: float = 1.0
    max_trade_spread: float = MAX_TRADE_SPREAD
    candle_seconds: int = 5
    start_time: float = START_TIME

    def __post_init__(self):
        integers = {"num_events", "num_levels", "seed", "maker_half_spread_ticks",
                    "maker_order_size", "initial_inventory", "candle_seconds"}
        for name, limits in CONFIG_LIMITS.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
            if name in integers and int(value) != value:
                raise ValueError(f"{name} must be an integer")
            if not limits["min"] <= value <= limits["max"]:
                raise ValueError(f"{name} must be between {limits['min']} and {limits['max']}")
            if name in integers:
                setattr(self, name, int(value))
        if not math.isclose(self.tick_size * 10000, round(self.tick_size * 10000), rel_tol=0, abs_tol=1e-8):
            raise ValueError("tick_size must be a multiple of $0.0001")
        if not math.isclose(self.max_trade_spread * 10000, round(self.max_trade_spread * 10000), rel_tol=0, abs_tol=1e-8):
            raise ValueError("max_trade_spread must be a multiple of $0.0001")
        if not math.isclose(self.initial_price / self.tick_size, round(self.initial_price / self.tick_size), rel_tol=0, abs_tol=1e-7):
            raise ValueError("initial_price must align with tick_size")
        if self.initial_price < 50 * self.tick_size:
            raise ValueError("initial_price must be at least 50 ticks to seed the book")
        if self.start_time + self.duration_seconds > 86400:
            raise ValueError("start_time + duration_seconds must fit within one day")


@dataclass
class _Order:
    id: int
    side: int
    price: int
    size: int
    owner: str = "background"


def spread_allows_trade(bid_price: int, ask_price: int, maximum_spread: int) -> bool:
    """Inclusive comparison in LOBSTER integer units, without float rounding."""
    return 0 < ask_price - bid_price <= maximum_spread


class _Book:
    """OrderedDict insertion order supplies FIFO within each price level."""
    def __init__(self):
        self.orders = {}
        self.levels = {1: {}, -1: {}}

    def copy(self):
        copied = _Book()
        for order in self.orders.values():
            copied.add(_Order(order.id, order.side, order.price, order.size, order.owner))
        return copied

    def add(self, order):
        if order.id in self.orders:
            raise ValueError("duplicate order ID")
        self.orders[order.id] = order
        self.levels[order.side].setdefault(order.price, OrderedDict())[order.id] = order

    def remove(self, order_id, quantity):
        order = self.orders[order_id]
        if quantity <= 0 or quantity > order.size:
            raise ValueError("invalid removed quantity")
        order.size -= quantity
        if not order.size:
            del self.levels[order.side][order.price][order.id]
            if not self.levels[order.side][order.price]:
                del self.levels[order.side][order.price]
            del self.orders[order.id]

    def prices(self, side):
        return sorted(self.levels[side], reverse=side == 1)

    def best(self, side):
        prices = self.levels[side]
        return max(prices) if side == 1 else min(prices)

    def snapshot(self, depth=10):
        asks, bids = self.prices(-1)[:depth], self.prices(1)[:depth]
        if len(asks) != depth or len(bids) != depth:
            raise ValueError("insufficient visible depth")
        row = []
        for ask, bid in zip(asks, bids):
            row.extend([ask, sum(o.size for o in self.levels[-1][ask].values()),
                        bid, sum(o.size for o in self.levels[1][bid].values())])
        return row


def simulate(config: Config | dict | None = None) -> dict:
    """Generate exactly num_events events and independently replay-check them."""
    cfg = config if isinstance(config, Config) else Config(**(config or {}))
    # Recheck mutable Config instances passed by callers.
    cfg = Config(**asdict(cfg))
    rng = np.random.default_rng(cfg.seed)
    tick = int(round(cfg.tick_size * 10000))
    maximum_spread = int(round(cfg.max_trade_spread * 10000))
    center = int(round(cfg.initial_price * 10000))
    book = _Book()
    next_id = 1
    owners = {}

    def new_order(side, price, size, owner="background"):
        nonlocal next_id
        order = _Order(next_id, side, int(price), int(size), owner)
        next_id += 1
        return order

    # A documented pre-event seed: 16 distinct levels on each side. The maker
    # is first in the queue at the best bid and ask; other seed liquidity is
    # background-owned. Subsequent additions always have type-1 messages.
    for side in (1, -1):
        for level in range(16):
            price = center - side * (cfg.maker_half_spread_ticks + level) * tick
            if level == 0:
                book.add(new_order(side, price, cfg.maker_order_size, "maker"))
            book.add(new_order(side, price, int(rng.integers(60, 181))))
    initial_orders = [asdict(o) for o in book.orders.values()]
    owners.update({str(o.id): o.owner for o in book.orders.values()})

    # Normalized exponential gaps are Poisson arrival spacings conditional on
    # the count/window. All mutations of one incoming action share a timestamp.
    gaps = rng.exponential(1.0, cfg.num_events + 1)
    timestamps = cfg.start_time + cfg.duration_seconds * np.cumsum(gaps[:-1]) / gaps.sum()
    messages, snapshots, events = [], [], []
    buyer = seller = last_price = float(cfg.initial_price)
    last_action_time = float(cfg.start_time)
    cash, inventory = float(cfg.initial_cash), cfg.initial_inventory
    baseline_equity = cash + inventory * cfg.initial_price
    exchange_fees = maker_fees = 0.0
    maker_trade_edge = maker_inventory_revaluation = 0.0
    previous_midpoint = cfg.initial_price
    volume = trade_count = maker_fills = spread_blocked_orders = 0
    action_number = 0

    def plan_add(shadow, plan, order):
        shadow.add(order)
        plan.append((1, order.id, order.size, order.price, order.side, order.owner))

    def plan_remove(shadow, plan, kind, order_id, quantity):
        order = shadow.orders[order_id]
        # Inject actual liquidity BEFORE removing the final order at level ten.
        # This is an explicit event and is counted in the output event budget.
        if (quantity == order.size and len(shadow.levels[order.side][order.price]) == 1
                and len(shadow.levels[order.side]) <= cfg.num_levels):
            deepest = shadow.prices(order.side)[-1]
            price = deepest - order.side * tick
            if price <= 0:
                # At the positive-price boundary, replenishing the same price
                # preserves depth and FIFO instead of inventing invalid prices.
                price = order.price
            plan_add(shadow, plan, new_order(order.side, price, int(rng.integers(80, 241))))
        plan.append((kind, order.id, quantity, order.price, order.side, order.owner))
        shadow.remove(order.id, quantity)

    def submission_plan(side, limit, quantity):
        shadow, plan = book.copy(), []
        spread_blocked = False
        while quantity:
            opposite = -side
            best = shadow.best(opposite)
            crosses = limit >= best if side == 1 else limit <= best
            if not crosses:
                break
            if not spread_allows_trade(shadow.best(1), shadow.best(-1), maximum_spread):
                # This is a trader's willingness-to-cross rule. The trader
                # replaces the unfilled aggressive remainder with a passive
                # order at its own best quote. Never leave a crossed book or
                # force two incompatible resting limits to trade.
                limit = shadow.best(side)
                spread_blocked = True
                break
            resting = next(iter(shadow.levels[opposite][best].values()))
            filled = min(quantity, resting.size)
            plan_remove(shadow, plan, 4, resting.id, filled)
            quantity -= filled
        if quantity:
            plan_add(shadow, plan, new_order(side, limit, quantity))
        return plan, spread_blocked

    def maker_plan():
        shadow, plan = book.copy(), []
        for order in list(shadow.orders.values()):
            if order.owner == "maker":
                plan_remove(shadow, plan, 3, order.id, order.size)
        midpoint = (shadow.best(1) + shadow.best(-1)) / 2
        halfspread = cfg.maker_half_spread_ticks * tick
        # The dealer quotes around the current book, improving wide spreads.
        bid = min(shadow.best(-1) - tick, int(math.floor((midpoint - halfspread) / tick)) * tick)
        ask = max(shadow.best(1) + tick, int(math.ceil((midpoint + halfspread) / tick)) * tick)
        bid = max(tick, bid)
        ask = max(bid + tick, ask)
        plan_add(shadow, plan, new_order(1, bid, cfg.maker_order_size, "maker"))
        plan_add(shadow, plan, new_order(-1, ask, cfg.maker_order_size, "maker"))
        return plan

    while len(messages) < cfg.num_events:
        # Keep numeric timestamps within Excel's precision so workbook and CSV
        # cells match exactly. Clamp the rounded value to the session bounds.
        now = min(cfg.start_time + cfg.duration_seconds,
                  max(cfg.start_time, round(float(timestamps[len(messages)]), 9)))
        dt = (now - last_action_time) / 60.0
        last_action_time = now
        # Buyer GBM is exact for a time step. Seller OU uses its exact Gaussian
        # transition with last_price fixed during this step; for kappa=0 it
        # becomes arithmetic Brownian motion with seller drift. Both noises are
        # independent. This avoids unstable Euler steps at low event counts.
        buyer *= math.exp((cfg.buyer_drift - 0.5 * cfg.buyer_volatility ** 2) * dt
                          + cfg.buyer_volatility * math.sqrt(dt) * float(rng.normal()))
        kappa = cfg.seller_mean_reversion
        if kappa:
            decay = math.exp(-kappa * dt)
            seller = (seller * decay + last_price * (1 - decay)
                      + cfg.seller_drift * cfg.initial_price * (1 - decay) / kappa
                      + cfg.seller_volatility * cfg.initial_price
                      * math.sqrt(-math.expm1(-2 * kappa * dt) / (2 * kappa)) * float(rng.normal()))
        else:
            seller += (cfg.seller_drift * cfg.initial_price * dt
                       + cfg.seller_volatility * cfg.initial_price * math.sqrt(dt) * float(rng.normal()))
        # A transparent numerical boundary, not a market calibration: preserve
        # positive prices and room for a depth ladder during extreme scenarios.
        valuation_floor = 50 * cfg.tick_size
        buyer, seller = max(valuation_floor, buyer), max(valuation_floor, seller)
        action_number += 1
        spread_blocked = False
        if action_number % 12 == 0:
            plan = maker_plan()
        elif rng.random() < cfg.cancellation_probability:
            candidates = [o for o in book.orders.values() if o.owner == "background"]
            if candidates:
                order = candidates[int(rng.integers(len(candidates)))]
                partial = order.size > 1 and rng.random() < 0.55
                quantity = int(rng.integers(1, order.size)) if partial else order.size
                shadow, plan = book.copy(), []
                plan_remove(shadow, plan, 2 if partial else 3, order.id, quantity)
            else:
                plan = maker_plan()
        else:
            side = 1 if rng.random() < cfg.buyer_probability else -1
            value = buyer if side == 1 else seller
            # Two-tick idiosyncratic limit-price variation still permits trading
            # when both stochastic volatilities are zero.
            raw_ticks = value / cfg.tick_size + float(rng.normal(0, 2))
            limit_ticks = math.floor(raw_ticks) if side == 1 else math.ceil(raw_ticks)
            limit = max(25, int(limit_ticks)) * tick
            plan, spread_blocked = submission_plan(side, limit, int(rng.integers(20, 201)))
        if len(plan) > cfg.num_events - len(messages):
            # Never truncate an incoming order halfway through matching. A final
            # harmless passive submission fills a one-event remainder instead.
            side = 1 if rng.random() < cfg.buyer_probability else -1
            order = new_order(side, book.best(side), int(rng.integers(10, 101)))
            plan = [(1, order.id, order.size, order.price, order.side, order.owner)]
            spread_blocked = False

        blocked_submission_id = plan[-1][1] if spread_blocked else None
        for kind, oid, size, price, side, owner in plan:
            if kind == 1:
                book.add(_Order(oid, side, price, size, owner))
                owners[str(oid)] = owner
                if oid == blocked_submission_id:
                    spread_blocked_orders += 1
            else:
                book.remove(oid, size)
                if kind == 4:
                    last_price = price / 10000.0
                    notional = size * last_price
                    fee = notional * cfg.fee_bps / 10000.0
                    exchange_fees += fee
                    volume += size
                    trade_count += 1
                    if owner == "maker":
                        maker_fills += 1
                        maker_fees += fee / 2
                        maker_trade_edge += side * size * (previous_midpoint - last_price)
                        inventory += side * size
                        cash -= side * notional + fee / 2
            row = book.snapshot(cfg.num_levels)
            best_ask, best_bid = row[0] / 10000.0, row[2] / 10000.0
            midpoint = (best_bid + best_ask) / 2
            # Mark the fill first at the pre-event midpoint, then revalue all
            # post-fill inventory at the new midpoint. Their sum is gross P&L;
            # trade edge alone is not realized round-trip spread profit.
            maker_inventory_revaluation += inventory * (midpoint - previous_midpoint)
            previous_midpoint = midpoint
            equity = cash + inventory * midpoint
            pnl = equity - baseline_equity
            messages.append([now, kind, oid, size, price, side])
            snapshots.append(row)
            events.append({
                "time": now, "type": kind, "price": price / 10000.0,
                "size": size, "side": side, "best_bid": best_bid,
                "best_ask": best_ask, "last_price": last_price,
                "buyer_value": buyer, "seller_value": seller,
                "cash": cash, "inventory": inventory, "equity": equity,
                "pnl": pnl, "pnl_before_fees": pnl + maker_fees,
                "maker_trade_edge": maker_trade_edge,
                "maker_inventory_revaluation": maker_inventory_revaluation,
                "exchange_fees": exchange_fees,
                "volume": volume, "trade_count": trade_count,
                "maker_fills": maker_fills, "maker_fees_paid": maker_fees,
                "spread_blocked_orders": spread_blocked_orders,
                "resting_owner": owner,
            })

    candles_by_time = {}
    for event in events:
        if event["type"] != 4:
            continue
        bucket = cfg.start_time + math.floor((event["time"] - cfg.start_time) / cfg.candle_seconds) * cfg.candle_seconds
        price = event["price"]
        if bucket not in candles_by_time:
            candles_by_time[bucket] = {"time": bucket, "open": price, "high": price,
                                       "low": price, "close": price, "volume": 0}
        candle = candles_by_time[bucket]
        candle["high"] = max(candle["high"], price)
        candle["low"] = min(candle["low"], price)
        candle["close"] = price
        candle["volume"] += event["size"]
    final = events[-1]
    summary = {name: final[name] for name in (
        "last_price", "best_bid", "best_ask", "pnl", "pnl_before_fees",
        "maker_trade_edge", "maker_inventory_revaluation", "equity", "cash", "inventory",
        "exchange_fees", "volume", "trade_count", "maker_fills", "maker_fees_paid",
        "spread_blocked_orders")}
    summary.update(spread=final["best_ask"] - final["best_bid"], event_count=len(messages))
    result = {"config": asdict(cfg), "initial_orders": initial_orders,
              "order_owners": owners, "messages": messages, "orderbook": snapshots,
              "events": events, "candles": list(candles_by_time.values()), "summary": summary}
    result["validation"] = validate_result(result)
    if not result["validation"]["passed"]:
        raise RuntimeError("Generated data failed replay validation: " + "; ".join(result["validation"]["errors"]))
    return result


def validate_result(result: dict) -> dict:
    """Independent replay using simple dictionaries, without engine methods.

    Determinism is verified by the automated tests (two separate simulations),
    not by claiming that one replay proves the random seed is reproducible.
    """
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    checks, errors = [], []
    try:
        cfg = Config(**result["config"])
        messages, snapshots = result["messages"], result["orderbook"]
        require(len(messages) == len(snapshots) == cfg.num_events, 'row counts/event budget')
        require(len(result['events']) == len(messages), 'display event count')
        checks.append("Exact event count and paired message/orderbook rows")
        tick = int(round(cfg.tick_size * 10000))
        live, seen = {}, set()
        for position, item in enumerate(result["initial_orders"]):
            oid = item["id"]
            require(oid not in seen, 'duplicate initial order ID')
            require(item['side'] in (-1, 1) and item['size'] > 0, 'invalid initial order')
            require(isinstance(item['price'], int) and item['price'] > 0 and (item['price'] % tick == 0), 'invalid seed price')
            live[oid] = dict(item, arrival=position)
            seen.add(oid)
        previous = cfg.start_time
        for index, (message, snapshot) in enumerate(zip(messages, snapshots)):
            require(len(message) == 6 and len(snapshot) == 40, f'schema at row {index}')
            time, kind, oid, size, price, side = message
            require(math.isfinite(time) and previous <= time <= cfg.start_time + cfg.duration_seconds, f'time at row {index}')
            previous = time
            require(all((isinstance(v, int) and (not isinstance(v, bool)) for v in message[1:])), f'integer event fields at row {index}')
            require(kind in (1, 2, 3, 4) and side in (-1, 1), f'event kind/side at row {index}')
            require(oid > 0 and size > 0 and (price > 0) and (price % tick == 0), f'price/size/id at row {index}')
            if kind == 1:
                require(oid not in seen, f'reused order ID at row {index}')
                opposite = [o["price"] for o in live.values() if o["side"] == -side]
                if opposite:
                    require(price < min(opposite) if side == 1 else price > max(opposite), f'crossing resting submission at row {index}')
                live[oid] = {"id": oid, "side": side, "price": price, "size": size,
                             "arrival": len(result["initial_orders"]) + index}
                seen.add(oid)
            else:
                require(oid in live, f'missing resting order at row {index}')
                order = live[oid]
                require(order['side'] == side and order['price'] == price, f'resting identity at row {index}')
                require(size <= order['size'], f'over-removal at row {index}')
                if kind == 2:
                    require(size < order['size'], f'partial cancellation deletes order at row {index}')
                if kind == 3:
                    require(size == order['size'], f'full cancellation is partial at row {index}')
                if kind == 4:
                    pre_bid = max(o["price"] for o in live.values() if o["side"] == 1)
                    pre_ask = min(o["price"] for o in live.values() if o["side"] == -1)
                    require(0 < pre_ask - pre_bid <= round(cfg.max_trade_spread * 10000),
                            f'execution exceeds spread threshold at row {index}')
                    eligible = [o for o in live.values() if o["side"] == side]
                    first = min(eligible, key=lambda o: (-side * o["price"], o["arrival"]))
                    require(first['id'] == oid, f'price/FIFO priority at row {index}')
                order["size"] -= size
                if not order["size"]:
                    del live[oid]
            bids, asks = {}, {}
            for order in live.values():
                levels = bids if order["side"] == 1 else asks
                levels[order["price"]] = levels.get(order["price"], 0) + order["size"]
            bid_prices, ask_prices = sorted(bids, reverse=True), sorted(asks)
            require(len(bids) >= 10 and len(asks) >= 10, f'depth at row {index}')
            require(bid_prices[0] < ask_prices[0], f'crossed book at row {index}')
            reconstructed = []
            for ask, bid in zip(ask_prices[:10], bid_prices[:10]):
                reconstructed.extend([ask, asks[ask], bid, bids[bid]])
            require(all((isinstance(v, int) and (not isinstance(v, bool)) and (v > 0) for v in snapshot)), f'snapshot numeric cells at row {index}')
            require(reconstructed == snapshot, f'snapshot replay mismatch at row {index}')
        checks.extend([
            "Six-column messages and 40-column integer book snapshots",
            "Chronological timestamps within the configured session",
            "Positive quantities, tick-aligned prices, and event types 1/2/3/4",
            "Unique IDs and valid partial/full removals of existing orders",
            "Executions obey price priority and FIFO at the resting price",
            "Every execution satisfies the inclusive pre-trade spread threshold",
            "At least ten real levels per side, sorted and strictly uncrossed",
            "Every post-event snapshot exactly matches independent replay",
        ])
    except (AssertionError, KeyError, TypeError, ValueError, IndexError, OverflowError) as exc:
        errors.append(str(exc) or type(exc).__name__)
    return {"passed": not errors, "checks": checks, "errors": errors}


def export_result(result: dict, directory, include_xlsx: bool = True) -> dict:
    """Write paired numeric CSVs, workbook, seed state, and proposed replay data."""
    validation = validate_result(result)
    if not validation["passed"]:
        raise ValueError("Refusing invalid export: " + "; ".join(validation["errors"]))
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    files = []
    for label, rows in (("message", result["messages"]), ("orderbook", result["orderbook"])):
        path = directory / f"SYNTH_{label}_10.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows(rows)
        files.append(str(path))
    seed_path = directory / "SYNTH_initial_book.json"
    seed_path.write_text(json.dumps({
        "schema": "synthetic-lobster-initial-book-v1", "price_scale": 10000,
        "initial_orders": result["initial_orders"], "order_owners": result["order_owners"],
        "note": "Seed state exists before the first CSV event; every later mutation is explicit."
    }, indent=2) + "\n", encoding="utf-8")
    files.append(str(seed_path))
    replay_path = directory / "SYNTH_replay.jsonl"
    with replay_path.open("w", encoding="utf-8") as handle:
        # Proposed transport-neutral format. This is not a firmware protocol.
        header = {"kind": "initial_state", "schema": "synthetic-replay-v1-proposed",
                  "price_scale": 10000, "config": result["config"],
                  "orders": result["initial_orders"]}
        handle.write(json.dumps(header, separators=(",", ":")) + "\n")
        start = result["config"]["start_time"]
        for index, (message, book) in enumerate(zip(result["messages"], result["orderbook"])):
            row = {"kind": "event", "sequence": index,
                   "offset_us": round((message[0] - start) * 1000000),
                   "message": message, "book": book,
                   "owner": result["order_owners"][str(message[2])]}
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    files.append(str(replay_path))
    metadata_path = directory / "SYNTH_metadata.json"
    metadata_path.write_text(json.dumps({
        "config": result["config"], "summary": result["summary"], "validation": validation,
        "units": {"prices_csv": "USD x10000", "time": "seconds after midnight",
                  "drift": "fractional initial-price/return per minute",
                  "volatility": "fractional scale per square-root minute",
                  "max_trade_spread": "USD; inclusive pre-fill spread limit",
                  "fee_bps": "basis points of executed notional, split equally between participants"},
        "accounting": {
            "pnl_before_fees": "maker_trade_edge + maker_inventory_revaluation",
            "pnl": "pnl_before_fees - maker_fees_paid",
            "maker_trade_edge": "Cumulative maker fills valued against the midpoint immediately before each fill; not realized round-trip profit.",
            "maker_inventory_revaluation": "Changes in inventory value as the book midpoint moves, including starting inventory."
        },
        "limitations": ["Educational synthetic data, not Nasdaq calibration.",
                        "ESP32 transport is proposed until firmware is available.",
                        "Maker may hold negative inventory; no borrowing/margin constraints.",
                        "Commission changes accounting but does not change trader behavior.",
                        "The spread threshold gates willing incoming traders; prices must still cross. Blocked remainders rest at their own side's best quote.",
                        "Agent valuations have a positive floor of 50 ticks.",
                        "Background liquidity replenishment is explicit in event counts."]
    }, indent=2) + "\n", encoding="utf-8")
    files.append(str(metadata_path))
    if include_xlsx:
        if len(result["messages"]) > 1048576:
            warnings.warn("Complete CSVs written; workbook skipped because Excel supports at most 1,048,576 rows per sheet.")
        else:
            from openpyxl import Workbook
            workbook = Workbook(write_only=True)
            for name, rows in (("message", result["messages"]), ("orderbook", result["orderbook"])):
                sheet = workbook.create_sheet(name)
                for row in rows:
                    sheet.append(row)
            workbook_path = directory / "SYNTH_market_data.xlsx"
            workbook.save(workbook_path)
            files.append(str(workbook_path))
    quotes_path = directory / "SYNTH_bid_ask_time.csv"
    with quotes_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("BID", "ASK", "Time"))
        for index, snapshot in enumerate(result["orderbook"]):
            writer.writerow((f"{snapshot[2] / 10000:.4f}", f"{snapshot[0] / 10000:.4f}", index))
    files.append(str(quotes_path))
    return {"directory": str(directory), "files": files}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=NUM_EVENTS)
    parser.add_argument("--seed", type=int, default=RANDOM_SEED)
    parser.add_argument("--duration", type=float, default=DURATION_SECONDS)
    parser.add_argument("--output", default="outputs/latest")
    parser.add_argument("--no-xlsx", action="store_true", help="Export CSV/JSON only")
    args = parser.parse_args()
    result = simulate(Config(num_events=args.events, seed=args.seed, duration_seconds=args.duration))
    exported = export_result(result, args.output, include_xlsx=not args.no_xlsx)
    print(f"Generated and independently validated {len(result['messages']):,} events.")
    print(f"Trades: {result['summary']['trade_count']:,}; maker fills: {result['summary']['maker_fills']:,}")
    print(f"Files: {exported['directory']}")


if __name__ == "__main__":
    main()
