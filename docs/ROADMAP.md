# Roadmap

## Milestone 1 — market truth layer
- [x] immutable order-book domain model
- [x] public Binance spot/perpetual depth streams
- [x] visible-depth fill simulation
- [x] explicit spread/slippage/fee/latency decomposition
- [x] spot/perpetual detector
- [x] stale-book and edge risk gates
- [x] JSONL research recorder
- [x] CI tests and linting
- [ ] long-running capture benchmark on real streams

## Milestone 2 — research & replay
- deterministic event replay
- opportunity lifetime tracking
- partial-fill and leg-risk simulator
- latency distributions rather than one fixed reserve
- per-symbol performance report
- dashboard metrics feed

## Milestone 3 — more arbitrage graphs
- triangular graph engine
- USDT/USDC/FDUSD synthetic paths
- funding/basis engine
- capital-aware opportunity allocator

## Milestone 4 — cross-exchange
- normalized venue adapters
- pre-funded inventory model
- transfer/rebalancing planner separated from opportunity execution
- venue health/rate-limit monitoring

## Milestone 5 — guarded execution
Only after paper evidence passes predefined acceptance criteria:
- order interface with trading-only credentials
- two-leg execution coordinator
- hedge/recovery state machine
- exposure limits and global kill switch
- audit log and operator dashboard

Live trading is intentionally absent until this milestone.
