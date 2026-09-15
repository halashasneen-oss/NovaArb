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
5. Venue/symbol health analytics measure feed delay, interarrival gaps, stale-gap ratios and out-of-order observations.
6. Public funding snapshots from the Binance USD-M premium-index endpoint.
7. Strategy scanners generate theoretical candidates.
8. Exchange rules enforce minimum quantity, notional, step-size and dust behavior where applicable.
9. Staleness and cross-book clock-skew gates reject asynchronous snapshots.
10. Pre-funded cross-venue gates require quote on the buy venue and base on the sell venue before a candidate is actionable in research.
11. `MultiAssetInventoryLedger` extends pre-funded accounting across many assets and venues instead of one base/quote pair.
12. `MultiInstrumentShadowEngine` evaluates many normalized instruments against the same shared venue/asset inventory.
13. `CapitalAwareAllocator` selects among simultaneous candidates before hypothetical execution.
14. Selected shadow candidates are repriced from the latest observed books instead of inheriting detection prices.
15. `ShadowRiskGuard` can halt shadow decisions for unhealthy data, daily-loss breaches, venue concentration or target-weight drift.
16. `ShadowLiveCoordinator` applies allocation, repricing, inventory enforcement and conservative accounting to public-data signals only.
17. Research capture stores raw books, funding updates and metadata in deterministic JSONL/gzip sessions.
18. Replay reconstructs opportunity windows, route statistics, funding projections, cross-venue delayed fills and shared shadow portfolio outcomes.
19. `SequentialTriangleSimulator` replaces simultaneous-fill assumptions with timed leg-by-leg execution.
20. Cross-venue replay reprices buy and sell legs independently on the first later venue book inside the configured latency wait budget.
21. `InventoryLedger` tracks single-instrument hypothetical pre-funded balances after simulated cross-venue fills.
22. `InventoryRebalancePlanner` estimates transfers required to restore target inventory shares and their modeled cost.
23. `EmergencyUnwinder` prices partial triangular exposure back to the anchor asset when possible.
24. Paper accounting measures busy capital, cooldowns, simulated PnL, drawdown and profit factor.
25. Fee-sensitivity replay retests identical captures under alternate taker-fee assumptions.

## Venue-health evidence

`novaarb-venue-health` consumes an existing JSONL/gzip capture and produces a machine-readable JSON report per venue, symbol and market. It reports event count, duration, event rate, feed-delay p50/p95/p99/max, interarrival p50/p95/p99/max, stale-gap counts/ratios and out-of-order counts/ratios.

Each stream is classified as `healthy`, `degraded` or `unhealthy` from explicit thresholds. This is research evidence, not exchange status truth: it describes the quality of the data received by the deployment environment.

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

## Shared shadow portfolio replay

`replay_shadow_portfolio` consumes one chronological multi-venue capture containing several canonical Spot instruments. All instruments share the same `MultiAssetInventoryLedger`, `CapitalAwareAllocator` and `ShadowRiskGuard`.

Approved detections are grouped into short allocation windows. The allocator chooses among simultaneous candidates using deployable capital, opportunity caps, position limits and resource conflicts. A selected detection still does not count as a fill: the engine retrieves the latest observed buy and sell books at allocation time, recalculates visible-depth economics and checks quantitative inventory again. If the edge has disappeared, the event is counted as decay rather than a zero-loss trade.

Completed hypothetical fills update venue/asset balances and record conservative net profit. The conservative research PnL keeps configured execution and rebalance reserves even though those reserves are not debited from the hypothetical cash ledger. This intentionally makes the acceptance evidence harder to pass than raw marked inventory gain.

`build_shadow_metrics` converts the replay summary into a stable JSON-ready operator payload with portfolio, venue, asset, symbol, allocation-rejection, kill-switch and optional venue-health sections.

## Foreground public shadow operation

`novaarb-shadow-run` wires the existing Binance and Bybit public adapters into `MultiInstrumentPublicShadowScanner` and `ShadowLiveCoordinator`. It runs in the foreground, accepts hypothetical pre-funded balances, batches simultaneous signals for shared-capital allocation, reprices selected opportunities from current public books, applies only hypothetical inventory changes and emits optional heartbeat metrics through the research recorder.

The runner has no authenticated exchange client. Running it for days or weeks is an operator/deployment action; the existence of the runner is not evidence that a multi-day profitability benchmark has already been completed.

## Shadow risk layer

The multi-asset ledger stores balances by `(venue, asset)`. A cross-venue shadow fill moves quote and base balances on the two venues while keeping unrelated assets untouched, which allows BTC, ETH and later instruments to share the same pre-funded inventory model.

The ledger can mark every nonzero asset into a common quote currency using observed reference prices. `ShadowRiskGuard` then evaluates total marked value, maximum venue concentration, maximum asset concentration, drift from configured venue target weights and conservative realized daily PnL. An unhealthy-data flag has the highest-priority kill switch.

## Capital allocation layer

The research allocator consumes normalized candidate economics rather than strategy-specific objects. It can enforce total deployable capital and cash reserve, maximum positions, maximum capital per opportunity, per-strategy position/capital limits, minimum expected edge, and resource exclusivity so overlapping markets cannot be double-allocated silently.

The v0.9 shadow path uses this allocator for multi-instrument cross-venue candidates. Funding and the remaining strategy families still need adapters into the same shadow candidate bus before the project can claim a truly cross-strategy portfolio replay.

## Safety boundary

There is no authenticated exchange client, order endpoint, withdrawal endpoint or transfer executor in v0.9. All Binance/Bybit shadow integration is public market data. Inventory, shadow-risk and rebalancing components are simulations used to expose capital constraints and hidden cross-exchange costs.

Live execution remains a separate future milestone behind reviewed interfaces, hard exposure limits, kill switches and sustained shadow evidence.

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
- long-running multi-instrument shadow run with no real orders across multiple market regimes
- predefined quantitative acceptance thresholds that must pass before authenticated execution code is introduced
