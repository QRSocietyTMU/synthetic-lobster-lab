# Contributing

Use this repository for QRSocietyTMU's Synthetic Market Lab development. Follow the setup steps in [README.md](README.md).

## Make a change

1. Create a branch with a descriptive name.
2. Keep changes focused on one behavior or fix.
3. Run `python -m unittest -v` in the project environment. For JavaScript changes, also run `node --check web/app.js` if Node.js is installed, and inspect the affected dashboard controls in a browser.
4. Update the README or [integration contract](CONTRACT.md) if settings, accounting, exports, or API behavior change.
5. Open a pull request describing the problem, resulting behavior, and verification performed.

## Preserve the model contract

- Keep matching deterministic for a fixed seed and unchanged configuration.
- Preserve price-time priority and reconstructable post-event book snapshots.
- Keep maker P&L separate from venue gross fee revenue.
- Include meaningful tests for matching, fee, spread-threshold, or accounting changes.
- Label ESP32 exports as proposed until verified against real firmware.

Do not commit credentials, virtual environments, caches, generated outputs, or original reference market data. Report reproducible bugs with the seed and relevant simulation settings.
