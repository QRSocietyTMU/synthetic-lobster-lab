# Synthetic Market Lab

**QRSocietyTMU · Interactive market simulation for student workshops**

A laptop-based Python market simulator for an ESP32 trading workshop. Adjust buyer and seller behavior, see the resulting trade candles and order book, and export a repeatable data set. This checkpoint runs without hardware. ESP32 delivery will be added when the firmware and its expected input format are available.

## What you can explore

- Adjust buyer and seller drift, volatility, and order flow with interactive sliders.
- Watch executed-trade candlesticks alongside bid/ask quotes and ten levels of market depth.
- Compare market-maker P&L before and after commission, with spread contribution and inventory revaluation shown separately.
- Restrict trades with an inclusive bid/ask spread threshold and inspect venue fee revenue.
- Replay seeded scenarios and export LOBSTER-format CSVs, Excel workbooks, and a proposed ESP32 replay stream.

**Status:** the laptop simulator is working. ESP32 transport and firmware integration are planned; no hardware connection is implemented yet. GitHub hosts the source code; Python must run locally to serve the dashboard.

## Start the dashboard

You need Python 3.10 or later and Git. Clone this repository, then enter its folder:

```sh
git clone https://github.com/QRSocietyTMU/synthetic-lobster-lab.git
cd synthetic-lobster-lab
```

For a private repository, sign in with a GitHub account that has access. You can also use **Code → Download ZIP** and extract it.

On macOS or Linux:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python server.py
```

On Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server.py
```

Open **http://127.0.0.1:8765** in a browser. Keep the terminal running. Stop it with Ctrl+C. Use `python server.py --port 8766` if the default port is occupied.

The dashboard runs on your laptop, requires no cloud account, and loads its chart code locally. Python calculates the market; the browser draws and replays it.

## Try it

1. Start with the Balanced preset and inspect the trade candles, quotes, and depth.
2. Raise buyer drift. Buyer valuations will tend to rise, changing the willingness to cross the ask. Price increases depend on the available liquidity and opposing orders.
3. Raise volatility on either side. Inspect the dispersion of valuations, executions, and spreads.
4. In **Fees & trading**, set Venue commission to zero, then increase it. Compare maker P&L before and after commission, and the venue's separate fee revenue.
5. Lower Max spread to trade to restrict executions to tighter quotes. Set it to zero to see a changing order book without executions.
6. Select Play to replay the generated run. Pause or scrub to inspect the book and accounts at that point.
7. Export the run to download the CSVs, workbook, initial book, settings, and proposed ESP32 replay stream.

Changing a control regenerates the whole scenario using the selected seed. It does not append a new regime to an existing history. Keeping the seed fixed helps compare settings; use a different seed to explore another realization. Replay speed changes viewing speed, not the simulated market.

## Controls and units

One model time unit is **one simulated minute**. Timestamps in exported messages remain **seconds after midnight**, with up to nine decimal places, as required by LOBSTER. This precision also lets the numeric workbook reproduce the CSV values exactly. The default run starts at 09:30 and covers five simulated minutes.

| Control | Meaning |
| --- | --- |
| Buyer drift | Fractional expected growth per minute in the buyer GBM valuation. UI 1% means Python `0.01`. Can be negative. |
| Buyer volatility | Fractional GBM noise scale per square root of a minute. UI 1% means `0.01`. Must be nonnegative. |
| Seller drift | Additional reservation-price drift per minute, expressed as a fraction of the initial stock price. Can be negative. |
| Seller volatility | Seller noise per square root of a minute, expressed as a fraction of the initial price. |
| Seller mean reversion | Pull toward the last executed trade price, per minute. Higher values strengthen the pull. |
| Buyer share | Probability of selecting the buy side for background order submissions. |
| Cancellations | Chance of a background cancellation attempt rather than a new background order. |
| Maker half-spread | Quoting distance in ticks on each side of the maker's reference price. A tick is $0.01 by default. |
| Venue commission | Total fee in basis points of executed notional, split equally between the two participants. Slider: 0–100 bps; default 1 bp. Zero removes all commissions; 1 bp is 0.01%, and 100 bps is 1%. |
| Max spread to trade | Largest current best-ask minus best-bid gap that permits an incoming order to execute. The limit is inclusive and in dollars. Slider: $0–$2; Python/API: $0–$10; default $1. |
| Events | Number of exported book-changing events, including maker activity and liquidity replenishment. |
| Seed | Reproduces a run for unchanged settings and software. |
| Candle interval | Length of a trade candle in simulated seconds. Buckets with no trades have no candle. |

Buyer GBM and seller mean reversion model **valuations**, not the displayed stock price. Incoming limits are influenced by those valuations. A price-time-priority matching engine determines trades at the resting order's price. The seller drift control extends the supplied brief's zero-drift OU process.

The spread threshold controls a trader's willingness to cross the book. With a $3.00 bid and $3.01 ask, a $0.01 threshold permits an execution; a smaller threshold blocks it. With a $3.00 bid and $4.00 ask, the threshold must be at least $1.00. In both examples, an incoming buyer must still be willing to pay the ask, or an incoming seller willing to accept the bid. Raising the threshold does not force existing, incompatible limit orders to trade.

The engine checks the spread before each fill, including when one incoming order sweeps several resting orders. If the gap becomes too wide, the unfilled remainder posts at the best quote on its own side. The dashboard counts these orders made passive by the threshold. At a zero threshold, there are no executions, commissions, or trade candles; submissions and cancellations can still move quotes and the value of held inventory.

## Two separate accounts

**Market maker:** an explicitly identified participant quotes on both sides. Its own fills change its cash and share inventory. Inventory can be negative: this educational model allows borrowing and shorting without a margin or interest model.

```text
equity = cash + inventory × current book midpoint
P&L after commission = equity − (initial cash + initial inventory × initial price)
P&L without commission = P&L after commission + maker commission paid
P&L without commission = spread contribution at fills + inventory price changes
```

Maker fees are deducted from cash only when the maker participates in a fill. The graph compares P&L after commission, P&L without commission, and the spread contribution. With all other settings and the seed unchanged, changing commission preserves the same orders, fills, and prices: fees affect accounting but do not alter trader behavior. At zero commission, the before- and after-commission P&L lines coincide.

**Spread contribution at fills** marks each maker purchase or sale against the book midpoint just before that fill. A purchase below the midpoint or a sale above it adds a positive contribution. **Inventory price changes** revalues all held shares, including starting inventory, whenever the midpoint moves. Together they give P&L before commission. Spread contribution alone is not realized round-trip profit, and zero fees do not guarantee a profitable maker. Inventory price changes can outweigh the contribution from fills, even when no new maker trade occurs.

**Exchange:** earns the configured fee on every simulated execution. This is gross fee revenue. Operating costs, rebates, taxes, and net exchange profit are not modeled. The interface does not label stock inventory as outstanding share float.

## Outputs and compatibility

- `SYNTH_message_10.csv`: no headers or index; six columns: time, type, order ID, shares, integer price, resting side.
- `SYNTH_orderbook_10.csv`: no headers or index; forty columns: ask price, ask size, bid price, bid size, repeated for ten occupied levels.
- `SYNTH_market_data.xlsx`: numeric `message` and `orderbook` sheets with the same rows and no headers.
- Supporting JSON files: initial resting orders with owners and settings; retain these to replay the synthetic messages from a known starting state.
- Replay JSONL: a proposed transport-neutral stream for future firmware integration. Exporting it does not send data to an ESP32.

Prices in the LOBSTER CSVs are **USD × 10,000**. For example, `2238200` means $223.82. Displayed chart prices are dollars. Message direction denotes the resting order: `+1` is a bid and `-1` is an ask. An execution with `-1` therefore comes from an incoming buy.

Each snapshot is the book **after its corresponding message**. The generator uses event types 1 (new order), 2 (partial cancellation), 3 (deletion), and 4 (visible execution). It does not invent hidden executions, auctions, or halts.

The initial seeded book is explicit. Later replenishment is recorded as new orders, so snapshots can be reconstructed from the initial book and events. Background liquidity supports ten visible levels; this stabilizes the educational demo and can influence apparent liquidity and prices. Valuations have a positive floor of 50 ticks. The seller uses the exact OU transition for each time step to avoid Euler instability at low event counts.

Candles aggregate execution prices and quantities only. Quote overlays and valuation paths are separate. No candles are fabricated in no-trade intervals.

## Validation

Every generated run must pass event replay before the API returns it. Validation checks row counts, schemas, timestamps, tick alignment, sorted depth, positive spread, the pre-fill spread threshold, eligible order references, quantities, and matching reconstructed snapshots. The 25-test suite also checks repeatability, candles, accounting, fee comparisons on identical market paths, exact threshold boundaries, partial sweeps, zero-trade scenarios, exports, and the HTTP workflow:

```sh
python -m unittest -v
```

For the standalone generator, run:

```sh
python generate_lobster.py
```

The script's help lists supported command-line overrides. Student configuration is at the top of `generate_lobster.py`.

## Workshop scenarios

The dashboard presets use these values. They change the participant controls while retaining the seed, event count, commission, and spread threshold.

| Preset | Buyer drift | Seller drift | Buyer / seller volatility | Seller reversion | Buyer share |
| --- | ---: | ---: | ---: | ---: | ---: |
| Balanced | 0%/min | 0%/min | 0.6%/√min each | 1.5/min | 50% |
| Rising | +0.5%/min | +0.3%/min | 0.6%/√min each | 0.7/min | 60% |
| Volatile | 0%/min | 0%/min | 1.8%/√min each | 0.5/min | 50% |

For an additional calm experiment, reduce both volatilities to 0.2%/√min and raise seller reversion to 3/min. Rising settings tend to increase buying willingness. Volatile settings tend to create more uneven order flow and liquidity. These are tendencies, not guarantees of price direction, spreads, execution counts, or profits.

## Current limits and next checkpoint

These are synthetic educational observations, not a calibration of Nasdaq or a copy of AMZN rows. The supplied files were used to verify the output structure. The model simplifies trader behavior, depth provision, fees, latency, and inventory constraints. Simulation speed is not an ESP32 throughput benchmark.

The browser previews and replays a generated batch. It does not yet change parameters mid-run, stream orders into a device, or confirm hardware execution. This prototype allows up to 10,000 events in the interface and 20,000 through Python. Default 3,000-event generation and validation took about one second on the development laptop; 10,000 took about 12 seconds. Performance depends on the scenario and machine.

After reviewing the interface and behavior, connect the future firmware's transport, payload, buffering, and acknowledgements to the validated event stream. `PROGRESS.md` records the saved milestone and the next decision so another session can resume without starting over.


## Repository guide

| File or folder | Purpose |
| --- | --- |
| `generate_lobster.py` | Market model, matching engine, accounting, validation, and exports. |
| `server.py` | Local HTTP API and dashboard server. |
| `web/` | Browser interface, charts, sliders, and replay controls. |
| `test_*.py` | Matching, accounting, threshold, export, and API checks. |
| `requirements.txt` | Python dependencies: NumPy and openpyxl. |
| [CONTRACT.md](CONTRACT.md) | Configuration, data fields, and API details. |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development and pull request workflow. |
| [PROGRESS.md](PROGRESS.md) | Development checkpoints and model decisions. |

Generated files go into `outputs/` and are excluded from Git. Original reference market data is not included in this repository.

## Roadmap

- Agree on the ESP32 payload, transport, buffering, and acknowledgement behavior once firmware is available.
- Implement device delivery and verify replay against the laptop's validated event sequence.
- Decide whether parameter changes should affect future orders during an ongoing run.
- Consider cash, inventory, and borrowing limits for capital-constraint demonstrations.

## Troubleshooting

- **The page will not open:** start `server.py` and keep its terminal open. Use the address printed by the server.
- **The port is already in use:** run `python server.py --port 8766`, then open `http://127.0.0.1:8766`.
- **A dependency is missing:** install `requirements.txt` using the same Python environment that runs the server.
- **There are no candles:** candles require executions. Check the spread threshold; zero intentionally prevents all trades.
- **Changes reset the chart:** sliders currently regenerate the entire scenario using the selected seed.

For a bug or workshop improvement, open an issue in this repository with the settings, seed, expected behavior, and observed result.
