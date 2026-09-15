# NovaArb

**Research-first arbitrage and price-dislocation engine.**

NovaArb is built around one non-negotiable rule:

> A visible price difference is not profit. It must survive fees, spread, depth slippage, timing, sequential execution, capital constraints, inventory drift, venue health and leg risk.

There is intentionally **no authenticated order client** in the repository. The current system is a public-market-data, deterministic-replay and shadow/paper-execution laboratory.

## What exists now

- Binance public Spot and USD-M Perpetual depth streams
- Bybit public V5 Spot/linear order-book adapter with snapshot/delta reconstruction
- generic normalized public-venue adapter contract
- normalized immutable order books and visible-depth fill simulation
- Spot ↔ Perpetual basis/dislocation research engine
- incremental closed-cycle Spot triangular arbitrage engine
- executable USDT ↔ USDC/FDUSD synthetic quote path planner
- conservative positive-funding long-Spot/short-Perpetual carry scanner
- self-contained funding capture and deterministic projected-carry replay
- pre-funded cross-venue opportunity model with per-venue fees and reserves
- Binance ↔ Bybit single-instrument public cross-venue scanner
- latency-aware cross-venue replay against later observed books
- multi-asset venue inventory ledger shared across multiple symbols
- venue concentration and inventory-weight drift risk limits
- daily-loss and abnormal-data shadow kill switches
- venue/symbol feed-health analytics for delay, stale gaps and out-of-order events
- machine-readable JSON metrics output for monitoring/dashboard ingestion
- research-only inventory rebalancing planner with transfer-cost estimates
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
public market data from one or more venues
       ↓
normalized market-truth books
       ↓
venue/symbol health gates
       ↓
theoretical opportunities
       ↓
fees / depth / staleness / clock-skew / inventory gates
       ↓
latency-aware replay against later observed books
       ↓
execution/recovery or multi-asset inventory-drift accounting
       ↓
shadow risk guard: concentration / drift / daily loss / abnormal data
       ↓
paper PnL + edge decay + rebalance-cost evidence
       ↓
capital-aware research allocation
```

The important number is not how many opportunities are detected. It is how many remain attractive after realistic execution, inventory, venue-health and capital assumptions.

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

# Machine-readable venue/symbol health metrics.
novaarb-venue-health data/cross-btc.jsonl.gz \
  --output data/venue-health.json

# Test alternate taker-fee assumptions on the same capture.
novaarb-fee-report data/triangle.jsonl.gz

# Direct-vs-synthetic quote cycles such as BTC/USDT vs BTC/USDC × USDC/USDT.
novaarb-synthetic-scan --alternative-quotes USDC FDUSD

# Public funding carry research. This reports projected carry, not realized profit.
novaarb-funding-scan --symbols BTCUSDT ETHUSDT --record data/funding.jsonl.gz
novaarb-funding-replay data/funding.jsonl.gz

# Public Binance ↔ Bybit pre-funded cross-venue research.
novaarb-cross-venue-scan \
  --symbol BTCUSDT \
  --base BTC \
  --quote USDT \
  --record data/cross-btc.jsonl.gz

# Reprice detected cross-venue signals after assumed venue latency and measure drift.
novaarb-cross-venue-replay data/cross-btc.jsonl.gz \
  --base BTC \
  --quote USDT \
  --buy-latency-ms 50 \
  --sell-latency-ms 50
```

All current exchange integration uses public market data. No Binance or Bybit API key is required.

## Cross-venue and shadow accounting model

Cross-exchange opportunities are modeled as **pre-funded**. Quote inventory must already exist on the venue used to buy and base inventory must already exist on the venue used to sell. NovaArb does not assume that coins can be transferred after a signal appears.

The multi-asset shadow ledger extends this from one symbol to many assets on the same venues. It can mark hypothetical balances into a common quote currency and evaluate venue concentration, target-weight drift, daily realized loss and abnormal-data health before a shadow decision is allowed.

The replay layer measures what happens after configurable execution latency, updates hypothetical venue inventories, reports realized edge decay and estimates what it would cost to rebalance inventory back toward the intended distribution. Transfer estimates are research outputs only; NovaArb never moves funds.

## Safety boundary

1. No live order endpoint exists.
2. No withdrawal or transfer capability exists.
3. Pre-funded venue balances are hypothetical research inventory, not connected account balances.
4. Secrets are excluded by `.gitignore`; future credentials must come from environment variables.
5. Partial sequences are never silently counted as zero-loss trades; exposure is explicitly modeled.
6. Funding results are labeled as projected carry until a holding/settlement/exit replay proves realized economics.
7. Shadow kill switches can halt research decisions on unhealthy data, daily-loss limits, venue concentration or inventory-weight drift.
8. Live execution will not be introduced until long-running capture and shadow results demonstrate a repeatable net edge across different market regimes.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Current status

**v0.8.0 — the research stack now adds venue-health evidence, machine-readable monitoring metrics, multi-asset pre-funded inventory accounting and shadow risk kill switches. The remaining work before any live interface is long-running real-market evidence, measured host latency, multi-instrument portfolio replay and consolidated shadow-operation metrics.**
