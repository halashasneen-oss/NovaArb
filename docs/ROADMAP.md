# Roadmap

## Milestone 1 — market truth layer
- [x] immutable order-book domain model
- [x] public Binance spot/perpetual depth streams
- [x] visible-depth fill simulation
- [x] explicit spread/slippage/fee/latency decomposition
- [x] spot/perpetual detector
- [x] stale-book and edge risk gates
- [x] versioned gzip-capable research capture
- [x] CI tests and linting

## Milestone 2 — deterministic research and replay
- [x] opportunity lifetime/window tracking
- [x] triangular route replay
- [x] cross-book timestamp-skew rejection
- [x] detected versus realized edge tracking
- [x] multiple deterministic latency profiles

## Milestone 3 — execution realism
- [x] sequential 3-leg fill simulation
- [x] later-book repricing per leg
- [x] partial execution and open-exposure reporting
- [x] emergency anchor-asset unwind simulation
- [x] conservative capital-aware paper portfolio
- [x] drawdown and profit-factor metrics

## Milestone 4 — real-market evidence
- [ ] long-running capture benchmark on real Binance streams
- [ ] latency p50/p95/p99 distributions from deployment host
- [ ] opportunity survival curves by route
- [ ] per-route/per-asset net performance report
- [ ] fee-tier sensitivity analysis
- [ ] dashboard metrics feed

## Milestone 5 — broader arbitrage graph
- [ ] USDT/USDC/FDUSD synthetic paths
- [ ] funding/basis carry engine
- [ ] capital-aware multi-strategy allocator
- [ ] cross-exchange normalized venue adapters
- [ ] pre-funded inventory and rebalancing model

## Milestone 6 — guarded live execution
Only after paper/shadow evidence passes predefined acceptance criteria:
- [ ] trading-only authenticated order interface
- [ ] atomic/sequenced execution coordinator
- [ ] hedge/recovery state machine
- [ ] exposure limits and global kill switch
- [ ] audit log and operator dashboard
- [ ] staged rollout with tiny capital

Live trading is intentionally absent until this milestone.
