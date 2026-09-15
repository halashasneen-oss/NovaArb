# NovaArb architecture

## Prime invariant

A visible price difference is never treated as profit.

For Spot/Perpetual basis research:

`gross dislocation - spread - depth slippage - fees - latency reserve - exit reserve - funding reserve = net capture potential`

For triangular and synthetic-quote research, the stronger test is sequential replay: every leg is repriced on the first actually observed book available after its scheduled execution time.

For funding carry, NovaArb reports **projected carry**, not realized profit:

`haircut expected funding - entry execution cost - fees - exit reserve - basis-risk reserve = projected net carry`

## Data and decision layers

1. Public Binance Spot and USD-M Perpetual depth streams.
2. Immutable `OrderBookSnapshot` market truth.
3. Public funding snapshots from the USD-M premium-index endpoint.
4. Strategy scanners generate theoretical candidates.
5. Exchange rules enforce minimum quantity, notional, step-size and dust behavior.
6. Staleness and cross-book clock-skew gates reject asynchronous snapshots.
7. Research capture stores raw books, funding updates and metadata in deterministic JSONL/gzip sessions.
8. Replay reconstructs opportunity windows, route statistics and funding-carry projections.
9. `SequentialTriangleSimulator` replaces simultaneous-fill assumptions with timed leg-by-leg execution.
10. `EmergencyUnwinder` prices partial exposure back to the anchor asset when possible.
11. Paper accounting measures busy capital, cooldowns, realized simulated PnL, drawdown and profit factor.
12. Capture-health analytics measure event rate, feed delay and interarrival gaps.
13. Fee-sensitivity replay retests identical captures under alternate taker-fee assumptions.
14. `CapitalAwareAllocator` ranks research candidates while enforcing cash, position, strategy and resource-conflict limits.

## Strategy families

### Spot / Perpetual basis

A basis gap is treated as deferred capture potential. Opening a hedged Spot/Perpetual pair does not realize the gap immediately, so closing costs and reserves are charged before a candidate can pass.

### Spot triangular arbitrage

The route is a closed cycle `A → B → C → A`. Every conversion uses executable bid/ask depth, exchange quantity rules, fees and dust. Replay can then reprice each leg at its later scheduled execution time.

### Synthetic quote dislocation

The planner isolates direct-vs-synthetic quote families such as:

`USDT → BTC → USDC → USDT`

and the reverse direction. It reuses the same exchange-aware triangular execution model rather than inventing a separate price-only shortcut.

### Funding carry

The implemented research direction is positive-funding `long Spot + short Perpetual`. Expected funding is haircut conservatively and charged against entry execution cost, taker fees, exit reserve and basis-risk reserve. Negative-funding carry requiring Spot short/margin is intentionally not modeled yet.

## Sequential execution model

A latency profile defines detection-to-first-leg delay, inter-leg delay and the maximum acceptable wait for a later book. For a triangle `A → B → C → A`, each leg uses the first recorded order book at or after its scheduled time. Fees, depth, rounding and dust are recalculated at every leg.

If execution fails after one or more legs, the result carries the open asset and amount. The recovery layer attempts a market unwind using the direct anchor pair from the route. If recovery cannot be priced, the default paper policy halts rather than inventing a zero-loss outcome.

## Capital allocation layer

The research allocator consumes normalized candidate economics rather than strategy-specific objects. It can enforce:

- total deployable capital and cash reserve
- maximum positions
- maximum capital per opportunity
- per-strategy position and capital limits
- minimum expected edge
- resource exclusivity so overlapping markets cannot be double-allocated silently

This is still a research selection layer; it is not an order router.

## Safety boundary

There is no authenticated exchange client, order endpoint or withdrawal endpoint in v0.6. Live execution remains a separate future milestone behind reviewed interfaces, hard exposure limits, kill switches and shadow evidence.

## Evidence required before live execution

- multi-day and multi-week raw market capture
- latency p50/p95/p99 from the actual deployment environment
- opportunity lifetime and edge-decay distributions
- paper results after actual account fee-tier assumptions
- funding settlement and exit replay rather than projected carry alone
- adverse selection after signal
- recovery frequency and recovery-loss distribution
- route/symbol/regime breakdowns
- multi-venue synchronization and inventory-risk tests
- global exposure and kill-switch tests
- shadow run with no real orders across multiple market regimes
