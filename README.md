# NovaArb

**Research-first arbitrage and price-dislocation engine.**

NovaArb is being built around one non-negotiable rule:

> A price difference is not an opportunity until it remains profitable after spread, depth slippage, fees, latency and execution risk.

The first milestone focuses on Binance Spot ↔ USD-M Perpetual dislocations using public order-book data. There is intentionally **no live order client** in the codebase yet.

## What exists now

- Binance public Spot and USD-M Perpetual partial-depth streams (20 levels, 100 ms)
- normalized immutable order books
- visible-depth VWAP/fill simulation
- executable-edge decomposition
- spot/perpetual cash-and-carry scanner
- stale-data, minimum-edge and max-notional risk gates
- JSONL recording of evaluated opportunities for later replay
- unit tests + GitHub Actions CI across Python 3.11–3.13

## Edge model

NovaArb calculates:

```text
mid-market dislocation
- bid/ask spread cost
- depth slippage
- taker fees
- latency reserve
- funding reserve (when relevant)
= executable net edge
```

Spread and slippage are deliberately separated for diagnostics and never subtracted twice.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install -e '.[dev]'
pytest

novaarb --symbols BTCUSDT ETHUSDT BNBUSDT \
  --notional 50 \
  --min-edge-bps 2 \
  --record data/opportunities.jsonl
```

The scanner uses **public market data only**. No Binance API key is required.

## Project principles

1. Execution prices come from bid/ask depth, never `lastPrice`.
2. Stale books fail closed.
3. Costs are explicit and auditable.
4. Research events are recorded, including rejected opportunities.
5. Live trading is not added until replay and paper/shadow results prove an edge over multiple market regimes.
6. API secrets must never be committed. Future credentials will be loaded from environment variables and trading-only permissions will be required.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Current status

**Milestone 1: market-truth layer — in progress.**
