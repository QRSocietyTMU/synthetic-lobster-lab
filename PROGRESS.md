# Resume checkpoint

Updated: 2026-10-28. **Milestone 2 complete: commission comparison and trade spread threshold, validated and ready for user review.** Pause here before starting the next milestone.

## Agreed decisions

- Python runs on a laptop. An ESP32 will later receive and replay generated data.
- Show market-maker cash, inventory and marked-to-market P&L separately from exchange gross fee revenue.
- ESP32 firmware/repository is not available yet. Do not invent or claim a hardware connection.
- Work in stages and stop for user feedback at milestones. Keep a saved checkpoint before pausing.
- User supplied LOBSTER CSVs are format references, not data to copy or a calibrated model.

## Saved implementation

- `generate_lobster.py`: real price-time/FIFO book, GBM buyer, OU seller with drift, explicit maker and liquidity events, exact event budget, replay validation, CSV/XLSX/JSON export.
- `server.py`: laptop-only HTTP API and dashboard server, default port 8765.
- `web/`: offline vanilla JavaScript dashboard with sliders, trade candles, bid/ask lines, depth, replay, separate maker/exchange panels.
- `test_simulator.py`, `test_server.py`, `test_commission.py`, `test_spread_threshold.py`: replay, accounting, exports, HTTP, fee comparison and spread boundary checks.
- `requirements.txt`: NumPy and openpyxl dependencies.
- `README.md`: setup, controls, accounting, limits, scenarios.
- `CONTRACT.md`: internal interface specification.
- `outputs/checkpoint-2/`: a validated 3,000-event sample with paired CSVs, numeric workbook, initial book, proposed replay JSONL, and metadata.
- `../Synthetic-Market-Lab-Checkpoint-2.zip`: current portable source and sample package, including this checkpoint. The separate milestone 1 archive is preserved.

## Verification already performed

- Source files: 269,748 rows in each, 6 message columns, 40 book columns; chronological times and correctly ordered ten-level quotes.
- Final run: **25 tests passed in 2.412 seconds** (12 core, four HTTP, four commission and five spread-threshold checks). JavaScript syntax check passed.
- Deliberately corrupted snapshots and overfills are rejected, including under optimized Python (`-O`). High-price tick alignment is checked.
- Timestamps are rounded to nine decimals before model updates. Every numeric cell in both exported sample workbook sheets matches its corresponding CSV value exactly.
- Default sample: 3,000 events, 764 executions, 98 maker fills, 39,381 shares traded. Independent replay passed.
- Generation plus validation timing: about 1.13 seconds for 3,000 events; about 12 seconds for 10,000 on this laptop. Timing varies with scenario and machine.
- Browser verified: initial generation, drift-slider regeneration, Play/Pause, reset to initial book/accounts, smaller event count, zero-fee scenario, ZIP download, desktop layout, narrow-panel layout and control navigation. No browser errors observed during those checks.
- All ten depth levels now remain visible at narrow widths; chart/label text was enlarged. Browser viewport override was reset after responsive checks.
- Current preview is running at `http://127.0.0.1:8765/`. If it stops, follow README startup steps. The task's browser tab is marked as a deliverable.

## Milestone 2 behavior and checks

- Venue commission is visible under Fees & trading, from 0 to 100 basis points (0% to 1%). Total commission is split equally between execution participants; the maker pays only on its own fills.
- The maker chart compares after-commission P&L, before-commission P&L and spread contribution. The breakdown separates spread contribution, inventory price changes and maker fees.
- Spread contribution values each maker fill against the midpoint immediately before that fill. It is not realized round-trip profit. Starting inventory and subsequent inventory revaluation remain part of P&L.
- Commission changes preserve the simulated trade path. Tests and browser checks confirm that zero and positive fees change accounting, not orders or prices.
- Max spread to trade is an inclusive dollar limit: $0.01 permits a $3.00/$3.01 gap; $1.00 permits a $3.00/$4.00 gap. It still requires an incoming trader willing to cross the other side's price. This reasonable default was stated after an optional clarification question; change it if the user requests different semantics.
- The limit is rechecked before every fill. Blocked remainders become passive orders at their own side's best quote. The book remains uncrossed. The UI reports how many orders were made passive.
- Threshold zero blocks all executions and fees. Browser verified zero trades, zero spread contribution and no trade candles, while quote-driven inventory valuation could still change.
- The UI allows thresholds from $0 to $2; Python/API allow $0 to $10. Export metadata records the threshold, fee units and accounting definitions.
- Default seed 42: spread contribution $211.06; inventory revaluation -$3,673.98; pre-fee P&L -$3,462.92. At 1 bp, maker fees $74.55 and net P&L -$3,537.47. At 100 bps, maker fees $7,455.30 and net P&L -$10,918.22. These are sample outcomes, not expected or guaranteed profits.
- Live browser verified new controls, before/after comparison, no-trade state, visible chart and accounting layout. No console errors or warnings observed. No viewport override was needed in milestone 2.

## Next decision for the user

Should slider changes continue to regenerate an entire comparable run, or should they affect only future orders during an ongoing simulation? Ask for review of the current interface before expanding the scope. A future milestone can also add cash/inventory limits if the workshop should demonstrate capital constraints.

ESP32 integration remains pending the actual firmware/repository. Do not start hardware integration or claim delivery without it.

## Known model decisions

- Sliders regenerate a whole run using the selected seed. Play replays that generated history.
- Maker may borrow cash or short shares; no margin or borrowing-cost model.
- Exchange panel shows gross fees, not net profit.
- Candles use only executions; no-trade buckets are omitted.
- Explicit background liquidity maintains ten visible levels and affects market behavior.
- Positive valuation floor: 50 ticks. Prices inside book/CSV: USD × 10,000. Seller OU uses an exact time-step transition.
- Stochastic model time units: minutes. Export timestamps: seconds after midnight.
- ESP32 JSONL export is a proposed transport-neutral format, not a verified device protocol.

## Local verification tools

Bundled Python: `/Users/morty/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`

Bundled Node: `/Users/morty/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node`

Start preview from this folder with the bundled Python and `server.py`. A normal installation uses the README's virtual environment. Local server binding may require sandbox approval. The preview is a running local process; if a later session finds it stopped, restart it using these instructions. No original Boltline project files have been changed. Work is saved on disk and in the ZIP; no Git commit or remote publication was made.
