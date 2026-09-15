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
- Binance ↔ Bybit public cross-venue scanning
- latency-aware cross-venue replay against later observed books
- multi-asset venue inventory ledger shared across multiple symbols
- multi-instrument shared-capital shadow portfolio replay
- allocation-time opportunity repricing to expose edge decay before a hypothetical fill
- foreground long-running public shadow runner with heartbeat metrics
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
- deterministic capital-aware research allocator
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
allocation window + shared-capital selection
       ↓
reprice from latest observed books before hypothetical execution
       ↓
shadow risk guard: concentration / drift / daily loss / abnormal data
       ↓
multi-asset inventory accounting + conservative paper PnL
       ↓
edge-decay / rebalance-cost / operator evidence
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

# Replay several captured instruments through one shared shadow portfolio.
novaarb-shadow-replay data/cross-multi.jsonl.gz \
  --instrument BTC/USDT \
  --instrument ETH/USDT \
  --balance binance:USDT:1000 \
  --balance binance:BTC:0.02 \
  --balance binance:ETH:0.5 \
  --balance bybit:USDT:1000 \
  --balance bybit:BTC:0.02 \
  --balance bybit:ETH:0.5 \
  --capital 2000

# Foreground public-data shadow operation. It records hypothetical metrics only.
novaarb-shadow-run \
  --instrument BTC/USDT \
  --instrument ETH/USDT \
  --balance binance:USDT:1000 \
  --balance binance:BTC:0.02 \
  --balance binance:ETH:0.5 \
  --balance bybit:USDT:1000 \
  --balance bybit:BTC:0.02 \
  --balance bybit:ETH:0.5 \
  --capital 2000 \
  --record data/shadow-market.jsonl.gz \
  --metrics data/shadow-metrics.jsonl.gz
```

All current exchange integration uses public market data. No Binance or Bybit API key is required.

## Cross-venue and shadow accounting model

Cross-exchange opportunities are modeled as **pre-funded**. Quote inventory must already exist on the venue used to buy and base inventory must already exist on the venue used to sell. NovaArb does not assume that coins can be transferred after a signal appears.

The multi-asset shadow ledger extends this from one symbol to many assets on the same venues. A shared capital allocator selects among simultaneous candidates, then each selected candidate is repriced against the latest observed public books before the hypothetical fill. Quantitative inventory is checked again before every shadow fill.

The portfolio can be marked into a common quote currency and evaluated for venue concentration, target-weight drift, daily realized loss and abnormal-data health before a shadow decision is allowed. Conservative PnL retains execution and rebalance reserves rather than treating them as free profit.

The foreground shadow runner can keep collecting public books and heartbeat metrics for an operator-defined period or until interrupted. This is infrastructure for multi-day evidence collection; the repository does not claim that a multi-day profitability benchmark has already been completed.

## Safety boundary

1. No live order endpoint exists.
2. No withdrawal or transfer capability exists.
3. Pre-funded venue balances are hypothetical research inventory, not connected account balances.
4. Secrets are excluded by `.gitignore`; future credentials must come from environment variables.
5. Partial sequences are never silently counted as zero-loss trades; exposure is explicitly modeled.
6. Funding results are labeled as projected carry until a holding/settlement/exit replay proves realized economics.
7. Shadow kill switches can halt research decisions on unhealthy data, daily-loss limits, venue concentration or inventory-weight drift.
8. The long-running shadow command consumes public data only and cannot submit exchange orders.
9. Live execution will not be introduced until long-running capture and shadow results demonstrate a repeatable net edge across different market regimes.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Current status

**v0.9.0 — NovaArb now supports shared-capital multi-instrument shadow replay and a foreground public Binance/Bybit shadow runner with allocation, repricing, inventory enforcement, kill switches and machine-readable heartbeat evidence. The next research gate is sustained real-market capture on the intended deployment host, followed by cross-strategy shadow integration and quantitative acceptance criteria before any authenticated execution work.**
