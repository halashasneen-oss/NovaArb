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
- [x] multi-capture feed-delay/latency baseline report tooling
- [x] opportunity survival and edge-decay metrics
- [x] per-route performance breakdown
- [x] fee-tier sensitivity replay
- [x] consolidated research evidence report
- [ ] multi-day and multi-week real-market capture benchmark
- [ ] deployment-host latency baseline measured on the intended host
- [x] machine-readable dashboard metrics feed for venue health
- [x] machine-readable shadow portfolio metrics feed

## Milestone 5 — broader single-venue arbitrage graph
- [x] USDT/USDC/FDUSD synthetic quote paths
- [x] positive-funding long-Spot/short-Perpetual carry research
- [x] self-contained funding capture and deterministic projected-carry replay
- [x] funding settlement/holding-period modeled-realized replay against captured rates/books
- [x] capital-aware multi-strategy research allocator
- [ ] negative-funding carry research where Spot-short mechanics are explicitly modeled

## Milestone 6 — cross-exchange research
- [x] normalized public venue adapter interface
- [x] second public market-data venue adapter (Bybit V5)
- [x] executable pre-funded cross-venue Spot opportunity model
- [x] separate per-venue fee/execution/rebalance assumptions
- [x] pre-funded inventory ledger
- [x] transfer/rebalancing planner separated from opportunity execution
- [x] Binance ↔ Bybit public single-instrument scanner
- [x] latency-aware later-book cross-venue replay
- [x] inventory drift and modeled rebalance-cost reporting
- [ ] long-running Binance/Bybit capture benchmark
- [ ] measured venue-specific latency distributions from deployment host
- [x] venue/symbol stale-feed, delay and out-of-order health analytics
- [x] multi-instrument venue/asset inventory accounting
- [x] websocket disconnect and exchange rate-limit telemetry for venue adapters
- [x] cross-venue opportunity survival curves by symbol/direction
- [x] cross-venue fee-tier and rebalance-cost sensitivity matrix

## Milestone 7 — shadow portfolio research
- [x] multi-instrument cross-venue portfolio replay on one shared timeline
- [x] shared capital allocator plus quantitative multi-asset inventory enforcement
- [x] reprice selected opportunities at allocation time to expose edge decay
- [x] venue concentration limits
- [x] venue inventory-weight drift limits
- [x] daily loss and abnormal-data research kill switches
- [x] foreground long-running public shadow runner with no order/transfer client
- [x] heartbeat/operator metrics recording and final session metrics
- [x] shared candidate bus for cross-venue, funding, Spot/Perpetual, triangular and synthetic-quote opportunities
- [x] stateful funding settlement/holding-period lifecycle inside the unified shadow portfolio
- [x] unified public cross-venue + funding runner with persistent capital/resource locks
- [ ] funding scanner WebSocket telemetry integrated into unified heartbeat evidence
- [ ] lead/lag research adapter if that experimental family is promoted into the shared bus
- [ ] multi-day shadow-operation evidence benchmark on the intended deployment host
- [x] predefined quantitative acceptance gate for future authenticated execution work

## Milestone 8 — guarded live execution
Only after paper/shadow evidence passes predefined acceptance criteria:
- [ ] trading-only authenticated order interface
- [ ] atomic/sequenced execution coordinator
- [ ] hedge/recovery state machine
- [ ] exposure limits and global kill switch
- [ ] audit log and operator dashboard
- [ ] staged rollout with tiny capital

Live orders, withdrawals and transfer execution are intentionally absent until the required evidence exists.
