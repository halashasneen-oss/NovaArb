# NovaArb

**Research-first arbitrage and price-dislocation engine.**

NovaArb is built around one non-negotiable rule:

> A visible price difference is not profit. It must survive fees, spread, depth slippage, timing, sequential execution and leg risk.

There is intentionally **no authenticated order client** in the repository. The current system is a market-data, replay and paper-execution laboratory.

## What exists now

- Binance public Spot and USD-M Perpetual partial-depth streams
- normalized immutable order books and visible-depth fill simulation
- Spot ↔ Perpetual basis/dislocation research engine
- incremental closed-cycle Spot triangular arbitrage engine
- exchange-rule aware quantity, notional, dust and fee handling
- stale-book and cross-book time-skew rejection
- versioned JSONL/gzip raw market capture
- deterministic opportunity-window replay
- sequential 3-leg execution simulation against later observed books
- latency profiles for 25/50/100+ ms execution research
- detected-edge versus realized-edge decay measurement
- partial-fill/open-exposure detection
- emergency unwind simulation back to the anchor asset
- capital-aware paper portfolio with drawdown and profit-factor metrics
- unit tests and GitHub Actions CI

## Research pipeline

```text
public Binance books
       ↓
market-truth scanner
       ↓
approved theoretical opportunity
       ↓
sequential latency simulator
       ↓
actual later books for leg 1 → leg 2 → leg 3
       ↓
recovery model if a leg cannot complete
       ↓
paper portfolio / drawdown / profit factor
```

The important number is not how many opportunities are detected. It is how many remain profitable after sequential execution.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install -e '.[dev]'
pytest

# Capture triangular market data and theoretical opportunities.
novaarb triangle \
  --notional 100 \
  --record data/triangle.jsonl.gz

# Measure opportunity lifetime and route statistics.
novaarb triangle-replay data/triangle.jsonl.gz

# Compare the same approved signals under multiple latency assumptions.
novaarb execution-replay data/triangle.jsonl.gz

# Run capital-aware sequential paper execution with emergency unwind modeling.
novaarb paper data/triangle.jsonl.gz \
  --initial-balance 1000 \
  --first-leg-ms 50 \
  --inter-leg-ms 50
```

All current exchange integration uses public market data. No Binance API key is required.

## Safety boundary

1. No live order endpoint exists.
2. No withdrawal capability exists.
3. Secrets are excluded by `.gitignore` and future credentials must come from environment variables.
4. A partial sequence is never silently counted as a zero-loss trade; exposure is either explicitly recovered in simulation or the paper run halts.
5. Live execution will not be introduced until long-running capture and shadow results demonstrate a repeatable net edge across different market regimes.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Current status

**v0.4.0 — market truth, triangular discovery, deterministic replay, sequential execution, recovery and paper portfolio implemented. Real-market capture benchmarking is next.**
