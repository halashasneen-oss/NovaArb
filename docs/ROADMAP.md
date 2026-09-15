# Roadmap

## Milestone 1 — market truth layer
- [x] immutable order-book domain model
- [x] public Binance Spot/Perpetual depth streams
- [x] visible-depth fill simulation
- [x] explicit spread/slippage/fee/latency decomposition
- [x] Spot/Perpetual detector
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
- [x] conservative paper portfolio
- [x] drawdown and profit-factor metrics

## Milestone 4 — evidence tooling
- [x] capture-health event-rate and feed-delay analytics
- [x] opportunity survival and edge-decay metrics
- [x] per-route performance breakdown
- [x] fee-tier sensitivity replay
- [x] consolidated research evidence report
- [ ] multi-day and multi-week real-market capture benchmark
- [ ] deployment-host latency baseline
- [ ] dashboard metrics feed

## Milestone 5 — broader single-venue arbitrage graph
- [x] USDT/USDC/FDUSD synthetic quote paths
- [x] positive-funding long-Spot/short-Perpetual carry research
- [x] self-contained funding capture and deterministic replay
- [x] capital-aware multi-strategy research allocator
- [ ] funding settlement/holding-period realized replay
- [ ] negative-funding carry research where Spot-short mechanics are explicitly modeled

## Milestone 6 — cross-exchange research
- [ ] normalized venue adapter interface
- [ ] second public market-data venue adapter
- [ ] cross-venue clock/latency normalization
- [ ] executable cross-exchange opportunity model
- [ ] pre-funded inventory model
- [ ] transfer and rebalancing planner separated from opportunity execution
- [ ] venue health and rate-limit monitoring

## Milestone 7 — guarded live execution
Only after paper/shadow evidence passes predefined acceptance criteria:
- [ ] trading-only authenticated order interface
- [ ] atomic/sequenced execution coordinator
- [ ] hedge/recovery state machine
- [ ] exposure limits and global kill switch
- [ ] audit log and operator dashboard
- [ ] staged rollout with tiny capital

Live trading is intentionally absent until this milestone.
