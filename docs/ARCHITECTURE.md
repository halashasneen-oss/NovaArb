# NovaArb architecture

## Prime invariant

A visible price difference is never treated as profit.

For Spot/Perpetual basis research:

`gross dislocation - spread - depth slippage - fees - latency reserve - exit reserve - funding reserve = net capture potential`

For triangular and synthetic-quote research, the stronger test is sequential replay: every leg is repriced on the first actually observed book available after its scheduled execution time.

For funding carry, NovaArb reports **projected carry**, not realized profit:

`haircut expected funding - entry execution cost - fees - exit reserve - basis-risk reserve = projected net carry`

For cross-venue Spot research:

`sell proceeds - buy cost - venue fees - execution reserves - rebalance reserves = conservative net cross-venue edge`

## Data and decision layers

1. Public Binance Spot and USD-M Perpetual depth streams.
2. Public Bybit V5 Spot/linear order-book streams with local snapshot/delta reconstruction.
3. Generic `PublicVenueAdapter` normalization into immutable `NormalizedBook` objects.
4. Immutable `OrderBookSnapshot` market truth.
5. Public funding snapshots from the Binance USD-M premium-index endpoint.
6. Strategy scanners generate theoretical candidates.
7. Exchange rules enforce minimum quantity, notional, step-size and dust behavior where applicable.
8. Staleness and cross-book clock-skew gates reject asynchronous snapshots.
9. Pre-funded cross-venue gates require quote on the buy venue and base on the sell venue before a candidate is actionable in research.
10. Research capture stores raw books, funding updates and metadata in deterministic JSONL/gzip sessions.
11. Replay reconstructs opportunity windows, route statistics, funding projections and cross-venue delayed fills.
12. `SequentialTriangleSimulator` replaces simultaneous-fill assumptions with timed leg-by-leg execution.
13. Cross-venue replay reprices buy and sell legs independently on the first later venue book inside the configured latency wait budget.
14. `InventoryLedger` tracks hypothetical pre-funded balances after simulated cross-venue fills.
15. `InventoryRebalancePlanner` estimates transfers required to restore target inventory shares and their modeled cost.
16. `EmergencyUnwinder` prices partial triangular exposure back to the anchor asset when possible.
17. Paper accounting measures busy capital, cooldowns, simulated PnL, drawdown and profit factor.
18. Capture-health analytics measure event rate, feed delay and interarrival gaps.
19. Fee-sensitivity replay retests identical captures under alternate taker-fee assumptions.
20. `CapitalAwareAllocator` ranks research candidates while enforcing cash, position, strategy and resource-conflict limits.

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

### Pre-funded cross-venue Spot arbitrage

The same instrument is normalized across venues. The current public implementation can compare Binance and Bybit Spot books. A candidate is priced from visible depth in both books and charged separate taker fees plus execution and rebalance reserves.

The model requires inventory to be positioned before the signal: quote on the venue used to buy and base on the venue used to sell. It never assumes a blockchain or internal transfer can complete within the lifetime of a short arbitrage signal.

## Cross-venue latency replay

The replay uses the raw public books captured from each venue. At detection time it records the profitable direction, then schedules the hypothetical buy and sell independently using configurable per-leg latency. Each leg receives the first recorded book at or after its scheduled execution time, subject to a maximum wait budget.

The resulting fill is not allowed to inherit the detection price. Visible depth and fees are recalculated from the later venue books. Replay reports detected edge, realized edge, edge decay, modeled net PnL, missing future-book failures and inventory rejections.

After completed simulated fills, the inventory ledger reflects the resulting base/quote drift. A separate planner estimates the transfers needed to return to the initial target distribution. Those transfer instructions are research outputs only and are never executed.

## Capital allocation layer

The research allocator consumes normalized candidate economics rather than strategy-specific objects. It can enforce total deployable capital and cash reserve, maximum positions, maximum capital per opportunity, per-strategy position/capital limits, minimum expected edge, and resource exclusivity so overlapping markets cannot be double-allocated silently.

This is still a research selection layer; it is not an order router.

## Safety boundary

There is no authenticated exchange client, order endpoint, withdrawal endpoint or transfer executor in v0.7. All Binance/Bybit integration is public market data. Inventory and rebalancing components are simulations used to expose capital constraints and hidden cross-exchange costs.

Live execution remains a separate future milestone behind reviewed interfaces, hard exposure limits, kill switches and shadow evidence.

## Evidence required before live execution

- multi-day and multi-week raw market capture from every venue used
- venue-specific latency p50/p95/p99 from the actual deployment environment
- cross-venue clock-skew and stale-feed distributions
- opportunity lifetime and edge-decay distributions
- paper results after actual account fee-tier assumptions
- inventory utilization, drift and rebalance-cost distributions
- funding settlement and exit replay rather than projected carry alone
- adverse selection after signal
- recovery frequency and recovery-loss distribution
- route/symbol/regime breakdowns
- venue disconnect/reconnect and data-quality tests
- global exposure and kill-switch tests
- shadow run with no real orders across multiple market regimes
