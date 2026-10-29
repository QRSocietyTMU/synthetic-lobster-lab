# Prototype integration contract

Local educational simulator. Python owns matching, accounting and data; the browser displays and replays results. No ESP32 firmware or cloud deployment in this checkpoint.

## Python interface

`generate_lobster.py` provides `Config(**settings)`, `simulate(config) -> dict`, `validate_result(result) -> dict`, and `export_result(result, directory, include_xlsx=True)`.

Config fields (defaults): `initial_price=223.82`, `buyer_drift=0.0`, `buyer_volatility=0.006`, `seller_drift=0.0`, `seller_volatility=0.006`, `seller_mean_reversion=1.5`, `num_events=3000`, `num_levels=10`, `duration_seconds=300.0`, `tick_size=0.01`, `seed=42`, `buyer_probability=0.5`, `cancellation_probability=0.15`, `maker_half_spread_ticks=3`, `maker_order_size=100`, `initial_cash=1000000.0`, `initial_inventory=1000`, `fee_bps=1.0`, `max_trade_spread=1.0`, `candle_seconds=5`, `start_time=34200.0`.

Drifts are fractional returns per simulated minute; volatilities are fractional return scales per square-root minute. Seller OU uses initial_price to convert the fractional inputs to dollar drift/noise. Mean reversion is per minute.

`fee_bps` is total venue commission on executed notional, from 0 to 100 bps in both UI and API. Each participant pays half, and the maker ledger pays its share only on its own fills. Zero removes all commissions. One basis point is 0.01%; 100 bps is 1%. Changing only this value preserves messages, book snapshots, candles, and gross maker P&L for the same seed: fees do not feed back into order behavior.

`max_trade_spread` is an inclusive dollar limit on the best ask minus best bid immediately before each execution. The UI slider spans $0–$2 in $0.01 steps; Python/API accepts $0–$10 in multiples of $0.0001. The default is $1.00. Comparisons use integer USD × 10,000 units. A $3.00 bid / $3.01 ask passes a $0.01 limit, and $3.00 / $4.00 passes a $1.00 limit. An incoming order must also cross the resting price; the threshold never forces incompatible resting orders to match.

The threshold is checked again for every fill in a sweep. If it fails, the incoming order's unfilled remainder becomes a passive order at its own side's best quote, recorded as a normal type-1 message. At zero threshold, all executions are blocked: volume, trade count, commissions, and candles stay empty or zero. Quotes and marked inventory value can still change.

Result keys:

- `config`: effective config dict.
- `initial_orders`: seed orders with `id`, `side` (+1 bid, -1 ask), `price` (USD x10000 integer), `size`, `owner` ("maker" or "background").
- `order_owners`: mapping of string order IDs to "maker" or "background" for initial and later orders.
- `messages`: arrays [time, event_type, id, size, price, resting_side].
- `orderbook`: arrays of 40 integers, post-event top ten ask price/size, bid price/size.
- `events`: one object per message, keys `time`, `type`, `price` (dollars), `size`, `side`, `best_bid`, `best_ask`, `last_price`, `buyer_value`, `seller_value`, `cash`, `inventory`, `equity`, `pnl`, `pnl_before_fees`, `maker_trade_edge`, `maker_inventory_revaluation`, `maker_fees_paid`, `exchange_fees`, `volume`, `trade_count`, `maker_fills`, `spread_blocked_orders`, `resting_owner`. Best quotes are finite dollar values. `resting_owner` identifies the affected order's owner, including newly added orders.
- `candles`: trade-only buckets with `time`, `open`, `high`, `low`, `close`, `volume` (all prices dollars). Empty buckets omitted.
- `summary`: final values of `last_price`, `best_bid`, `best_ask`, `pnl`, `pnl_before_fees`, `maker_trade_edge`, `maker_inventory_revaluation`, `equity`, `cash`, `inventory`, `exchange_fees`, `volume`, `trade_count`, `maker_fills`, `maker_fees_paid`, `spread_blocked_orders`, plus `spread` (dollars) and `event_count`.
- `validation`: validation report, at least `passed` and `checks` (list of strings).

Maker is an explicitly owned quoting participant with cash/inventory accounting. Background buys/sells are separate participants. Initial portfolio equity is `initial_cash + initial_inventory * initial_price`; ongoing equity uses book midpoint. `pnl` is equity minus initial equity, after maker commission. All accounting values below are cumulative dollars through the current event:

| Field | Definition |
| --- | --- |
| `maker_fees_paid` | Half of each execution's total venue fee when the resting order belongs to the maker. |
| `pnl_before_fees` | `pnl + maker_fees_paid`; P&L for the same market path without maker commission. |
| `maker_trade_edge` | Sum of `side × size × (pre-event midpoint − execution price)` over maker fills. `side=+1` for a maker purchase and `-1` for a sale. Displayed as spread contribution at fills. |
| `maker_inventory_revaluation` | Sum of `post-event inventory × (new midpoint − previous midpoint)` over every event, starting from the configured initial price. Includes the value change of starting inventory. |
| `exchange_fees` | Total gross commission on all executions, including those without the maker. Venue operating costs are not modeled. |

Up to floating-point precision, `pnl_before_fees = maker_trade_edge + maker_inventory_revaluation`, and `pnl = pnl_before_fees − maker_fees_paid`. The marked spread contribution is not realized round-trip profit; inventory changes can outweigh it. At zero fees, before- and after-commission P&L coincide, but either can be negative.

`spread_blocked_orders` counts incoming orders whose unfilled remainder was made passive by the threshold. It increments when that passive type-1 message is emitted, including after partial execution. It does not count individual prevented fills. Any liquidity replenishment is also an explicit type-1 event.

## HTTP

`GET /api/config` returns `{defaults, limits}`. `POST /api/simulate` accepts a settings object and returns `{run_id, config, events, candles, summary, validation, initial_orders, book_frames, final_book}`. `book_frames` contains the full `orderbook` array, one post-event 40-column integer snapshot per event, for historical depth replay; `final_book` is its final row. `GET /api/export?run_id=...&format=zip` returns a zip with message/orderbook CSVs, workbook, initial book JSON, replay JSONL and config metadata. `GET /api/health` returns status. Invalid requests return `{error}` with a non-200 status. API runs on localhost only.

The frontend controls regeneration with debounce and ignores stale responses. Label regeneration clearly. Replay uses the generated history; it does not contact hardware or imply hardware delivery. The replay export is a proposed transport-neutral format until real firmware is available.

All returned simulations pass independent event replay, including the inclusive pre-fill spread check. The 25-test suite covers matching and exports, HTTP responses, accounting decomposition, identical market paths across fee rates, threshold boundaries, multi-fill sweeps, and zero-threshold behavior.
