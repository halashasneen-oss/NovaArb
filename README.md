# NovaArb

**Research-first arbitrage and price-dislocation engine.**

NovaArb is built around one non-negotiable rule:

> A visible price difference is not profit. It must survive fees, spread, depth slippage, timing, sequential execution, capital constraints and leg risk.

There is intentionally **no authenticated order client** in the repository. The current system is a public-market-data, deterministic-replay and paper-execution laboratory.

## What exists now

- Binance public Spot and USD-M Perpetual depth streams
- normalized immutable order books and visible-depth fill simulation
- Spot ↔ Perpetual basis/dislocation research engine
- incremental closed-cycle Spot triangular arbitrage engine
- executable USDT ↔ USDC/FDUSD synthetic quote path planner
- conservative positive-funding long-Spot/short-Perpetual carry scanner
- self-contained funding capture and deterministic projected-carry replay
- exchange-rule aware quantity, notional, dust and fee handling
- stale-book and cross-book time-skew rejection
- versioned JSONL/gzip raw market capture
- capture-health p50/p95/p99 feed-delay and gap analytics
- deterministic opportunity-window replay
- sequential 3-leg execution simulation against later observed books
- detected-edge versus realized-edge decay and route-survival analytics
- fee-tier sensitivity replay
- partial-fill/open-exposure detection and emergency unwind simulation
- conservative paper portfolio with drawdown and profit-factor metrics
- deterministic capital-aware multi-strategy opportunity allocator
- consolidated research evidence report
- CI on Python 3.11, 3.12 and 3.13

## Research pipeline

```text
public market data
       ↓
market-truth scanners
       ↓
theoretical opportunities
       ↓
fees / depth / staleness / clock-skew gates
       ↓
sequential latency replay where applicable
       ↓
recovery model for incomplete sequences
       ↓
paper portfolio + route survival + fee sensitivity
       ↓
capital-aware research allocator
       ↓
evidence report
```

The important number is not how many opportunities are detected. It is how many remain attractive after realistic execution assumptions and capital constraints.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install -e '.[dev]'
pytest

# Capture triangular market data and theoretical opportunities.
novaarb triangle --notional 100 --record data/triangle.jsonl.gz

# Opportunity lifetime / route statistics.
novaarb triangle-replay data/triangle.jsonl.gz

# Sequential execution and edge-decay matrix.
novaarb execution-replay data/triangle.jsonl.gz

# Capital-aware paper replay with emergency unwind modeling.
novaarb paper data/triangle.jsonl.gz --initial-balance 1000

# Feed quality and consolidated evidence.
novaarb-capture-report data/triangle.jsonl.gz
novaarb-report data/triangle.jsonl.gz

# Test alternate taker-fee assumptions on the same capture.
novaarb-fee-report data/triangle.jsonl.gz

# Direct-vs-synthetic quote cycles such as BTC/USDT vs BTC/USDC × USDC/USDT.
novaarb-synthetic-scan --alternative-quotes USDC FDUSD

# Public funding carry research. This reports projected carry, not realized profit.
novaarb-funding-scan --symbols BTCUSDT ETHUSDT --record data/funding.jsonl.gz
novaarb-funding-replay data/funding.jsonl.gz
```

All current exchange integration uses public market data. No Binance API key is required.

## Safety boundary

1. No live order endpoint exists.
2. No withdrawal capability exists.
3. Secrets are excluded by `.gitignore`; future credentials must come from environment variables.
4. Partial sequences are never silently counted as zero-loss trades; exposure is explicitly modeled.
5. Funding results are labeled as projected carry until a holding/settlement/exit replay proves realized economics.
6. Live execution will not be introduced until long-running capture and shadow results demonstrate a repeatable net edge across different market regimes.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Current status

**v0.6.0 — single-venue research stack now covers basis, triangular, synthetic quote paths, funding carry, execution decay, recovery, fee sensitivity and capital-aware allocation. The next engineering milestone is normalized multi-venue research and long-running real-market evidence.**
