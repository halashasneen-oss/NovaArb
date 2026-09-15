# NovaArb architecture

## Prime invariant

A visible price difference is never treated as profit.

For basis research:

`gross dislocation - spread - depth slippage - fees - latency reserve - exit reserve - funding reserve = net capture potential`

For triangular research, the stronger test is sequential replay: every leg is repriced on the first actually observed book available after its scheduled execution time.

## Data and decision layers

1. Public Binance Spot and USD-M Perpetual depth streams.
2. Immutable `OrderBookSnapshot` market truth.
3. Strategy scanners generate theoretical candidates.
4. Exchange rules enforce minimum quantity, notional, step-size and dust behavior.
5. Staleness and cross-book clock skew gates reject asynchronous snapshots.
6. Research capture stores raw books and metadata in deterministic JSONL/gzip sessions.
7. Replay reconstructs opportunity windows and route-level statistics.
8. `SequentialTriangleSimulator` replaces simultaneous-fill assumptions with timed leg-by-leg execution.
9. `EmergencyUnwinder` prices partial exposure back to the anchor asset when possible.
10. The paper portfolio accounts for busy capital, route cooldown, realized PnL, drawdown and profit factor.

## Sequential execution model

A latency profile defines:

- detection → first-leg delay
- delay between subsequent legs
- maximum acceptable wait for a future book

For a triangle `A → B → C → A`, each leg uses the first recorded order book at or after its scheduled time. Fees, depth, rounding and dust are recalculated at every leg. The result reports detected edge, realized edge and edge decay.

If execution fails after one or more legs, the result carries the open asset and amount. The recovery layer attempts a market unwind using the direct anchor pair from the route. If recovery cannot be priced, the default paper policy halts rather than inventing a zero-loss outcome.

## Safety boundary

There is no authenticated exchange client, order endpoint or withdrawal endpoint in v0.4. Live execution remains a separate future milestone behind a reviewed interface and hard risk controls.

## Planned engines

- [x] Spot / perpetual dislocation research
- [x] Spot triangular graph engine
- [ ] USDT/USDC/FDUSD synthetic path engine
- [ ] funding/basis carry engine
- [ ] cross-exchange pre-funded arbitrage
- [ ] lead/lag research model (directional, not risk-free arbitrage)

## Evidence required before live execution

- multi-day and multi-week raw market capture
- latency p50/p95/p99 from the deployment environment
- opportunity lifetime and edge-decay distributions
- paper results after actual account fee tier assumptions
- adverse selection after signal
- recovery frequency and recovery-loss distribution
- route/symbol/regime breakdowns
- global exposure and kill-switch tests
- shadow run with no real orders across multiple market regimes
