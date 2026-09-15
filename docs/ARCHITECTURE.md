# NovaArb architecture

## Prime invariant

A visible price difference is never treated as profit.

For Spot/Perpetual basis research:

`gross dislocation - spread - depth slippage - fees - latency reserve - exit reserve - funding reserve = net capture potential`

For triangular and synthetic-quote research, the stronger test is sequential replay: every leg is repriced on the first actually observed book available after its scheduled execution time.

For funding carry, the scanner first reports conservative projected carry:

`haircut expected funding - entry execution cost - fees - exit reserve - basis-risk reserve = projected net carry`

A separate settlement replay then uses captured public funding rates and later observed Spot/Perpetual exit books to measure modeled-realized holding-period economics.

For cross-venue Spot research:

`sell proceeds - buy cost - venue fees - execution reserves - rebalance reserves = conservative net cross-venue edge`

## Data and decision layers

1. Public Binance Spot and USD-M Perpetual depth streams.
2. Public Bybit V5 Spot/linear order-book streams with local snapshot/delta reconstruction.
3. Generic `PublicVenueAdapter` normalization into immutable `NormalizedBook` objects.
4. Immutable `OrderBookSnapshot` market truth.
5. Venue/symbol health analytics measure feed delay, interarrival gaps, stale-gap ratios and out-of-order observations.
6. `StreamTelemetry` records public WebSocket connection attempts, reconnects, disconnects, messages, emitted snapshots, parse failures, queue drops, backoff time and rate-limit evidence.
7. Public funding snapshots from the Binance USD-M premium-index endpoint.
8. Strategy scanners generate theoretical candidates.
9. Strategy-specific opportunities can be normalized into shared `ResearchCandidate` envelopes.
10. Exchange rules enforce minimum quantity, notional, step-size and dust behavior where applicable.
11. Staleness and cross-book clock-skew gates reject asynchronous snapshots.
12. Pre-funded cross-venue gates require quote on the buy venue and base on the sell venue before a candidate is actionable in research.
13. `MultiAssetInventoryLedger` extends pre-funded accounting across many assets and venues instead of one base/quote pair.
14. `MultiInstrumentShadowEngine` evaluates many normalized instruments against the same shared venue/asset inventory.
15. `ShadowCandidateBus` lets heterogeneous strategy families declare common capital and market-resource usage before allocation.
16. `CapitalAwareAllocator` selects among simultaneous candidates before hypothetical execution.
17. Selected cross-venue shadow candidates are repriced from the latest observed books instead of inheriting detection prices.
18. `ShadowRiskGuard` can halt shadow decisions for unhealthy data, daily-loss breaches, venue concentration or target-weight drift.
19. `ShadowLiveCoordinator` applies allocation, repricing, inventory enforcement and conservative accounting to public-data signals only.
20. Research capture stores raw books, funding updates and metadata in deterministic JSONL/gzip sessions.
21. Shadow heartbeat metadata can be enriched with current stream telemetry for operator evidence.
22. Replay reconstructs opportunity windows, route statistics, funding projections, funding settlements, cross-venue delayed fills and shared shadow portfolio outcomes.
23. `SequentialTriangleSimulator` replaces simultaneous-fill assumptions with timed leg-by-leg execution.
24. Cross-venue replay reprices buy and sell legs independently on the first later venue book inside the configured latency wait budget.
25. `InventoryLedger` tracks single-instrument hypothetical pre-funded balances after simulated cross-venue fills.
26. `InventoryRebalancePlanner` estimates transfers required to restore target inventory shares and their modeled cost.
27. `EmergencyUnwinder` prices partial triangular exposure back to the anchor asset when possible.
28. Paper accounting measures busy capital, cooldowns, simulated PnL, drawdown and profit factor.
29. Survival analysis measures how long approved cross-venue dislocations remain observable by direction.
30. Cost sensitivity replay retests identical captures under alternate taker-fee and rebalance-cost assumptions.
31. `ShadowAcceptanceCriteria` turns evidence requirements into an explicit machine-testable gate before any future authenticated work.

## Venue-health and transport evidence

`novaarb-venue-health` consumes an existing JSONL/gzip capture and produces a machine-readable JSON report per venue, symbol and market. It reports event count, duration, event rate, feed-delay p50/p95/p99/max, interarrival p50/p95/p99/max, stale-gap counts/ratios and out-of-order counts/ratios.

Each stream is classified as `healthy`, `degraded` or `unhealthy` from explicit thresholds. This is research evidence, not exchange status truth: it describes the quality of the data received by the deployment environment.

`StreamTelemetry` complements the capture-health report with transport-level evidence during live public-data shadow operation. It records connection attempts, successful connections, disconnects, received messages, emitted snapshots, parse errors, explicit rate-limit signals, adapter queue drops and cumulative reconnect backoff. `TelemetryResearchRecorder` embeds those counters in heartbeat/final metadata so a profitable-looking shadow session cannot hide repeated feed failures.

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

The implemented research direction is positive-funding `long Spot + short Perpetual`. The scanner haircuts expected funding and charges entry execution cost, taker fees, exit reserve and basis-risk reserve before an opportunity is approved. Negative-funding carry requiring Spot short/margin is intentionally not modeled yet.

Funding opportunities can enter the common candidate bus for capital/resource competition. Their scanner economics remain **projected** until settlement replay is applied.

`replay_funding_settlements` adds a separate modeled-realized evidence layer. It reconstructs approved entries from the funding capture, takes the latest captured public funding rate whose `next_funding_time_ms` matches each scheduled settlement, models the short-Perpetual funding payment, then closes the long Spot and short Perpetual using synchronized later order books and visible-depth fills. Entry and exit taker fees are charged again from actual modeled fills.

The settlement replay reports completed/incomplete positions, settlement count, funding received, Spot PnL, Perpetual PnL, fees, holding time, modeled-realized net PnL and edge. It is deterministic research evidence from public data, not an exchange account statement. The unified multi-strategy shadow portfolio still needs a persistent funding-position lifecycle before it can include this realized funding PnL directly.

### Pre-funded cross-venue Spot arbitrage

The same instrument is normalized across venues. The current public implementation can compare Binance and Bybit Spot books. A candidate is priced from visible depth in both books and charged separate taker fees plus execution and rebalance reserves.

The model requires inventory to be positioned before the signal: quote on the venue used to buy and base on the venue used to sell. It never assumes a blockchain or internal transfer can complete within the lifetime of a short arbitrage signal.

## Cross-venue latency replay

The replay uses the raw public books captured from each venue. At detection time it records the profitable direction, then schedules the hypothetical buy and sell independently using configurable per-leg latency. Each leg receives the first recorded book at or after its scheduled execution time, subject to a maximum wait budget.

The resulting fill is not allowed to inherit the detection price. Visible depth and fees are recalculated from the later venue books. Replay reports detected edge, realized edge, edge decay, modeled net PnL, missing future-book failures and inventory rejections.

After completed simulated fills, the inventory ledger reflects the resulting base/quote drift. A separate planner estimates the transfers needed to return to the initial target distribution. Those transfer instructions are research outputs only and are never executed.

## Cross-venue survival evidence

`analyze_cross_venue_survival` scans the same recorded public books but answers a different question from fill replay: **how long did an approved dislocation remain observable?**

Approved observations are collapsed into direction-specific windows such as `binance → bybit` or `bybit → binance`. The report includes window count, median and maximum lifetime, median peak edge, and survival rates at configurable thresholds such as 25ms, 50ms, 100ms, 200ms, 500ms and 1s.

Signal survival does not prove fillability. It is combined with the later-book latency replay so the project can distinguish persistent price dislocations from executable opportunities.

## Cross-venue cost sensitivity

`run_cross_venue_sensitivity_matrix` replays the **same capture** across a grid of taker-fee tiers and modeled inventory-transfer/rebalance costs. Every matrix cell reports completed/profitable trades, replay net PnL, rebalance cost, net after rebalance, realized edge and edge decay.

This is designed to prevent assumption shopping. A candidate edge that only works under an unrealistically favorable fee tier or zero rebalancing cost should fail the evidence process instead of being promoted to live work.

## Shared shadow portfolio replay

`replay_shadow_portfolio` consumes one chronological multi-venue capture containing several canonical Spot instruments. All instruments share the same `MultiAssetInventoryLedger`, `CapitalAwareAllocator` and `ShadowRiskGuard`.

Approved detections are grouped into short allocation windows. The allocator chooses among simultaneous candidates using deployable capital, opportunity caps, position limits and resource conflicts. A selected detection still does not count as a fill: the engine retrieves the latest observed buy and sell books at allocation time, recalculates visible-depth economics and checks quantitative inventory again.

The committed execution model intentionally preserves adverse movement after selection. If the market has moved against the committed route, the hypothetical fill can be negative; it is not silently converted into a rejection just because the new edge is unattractive. This prevents survivorship bias in shadow evidence.

Completed hypothetical fills update venue/asset balances and record conservative net profit. The conservative research PnL keeps configured execution and rebalance reserves even though those reserves are not debited from the hypothetical cash ledger. This intentionally makes the acceptance evidence harder to pass than raw marked inventory gain.

`build_shadow_metrics` converts the replay summary into a stable JSON-ready operator payload with portfolio, venue, asset, symbol, allocation-rejection, kill-switch and optional venue-health sections.

## Shared multi-strategy candidate bus

`ShadowCandidateBus` normalizes strategy-specific opportunities into `CandidateEnvelope` objects backed by the existing `ResearchCandidate` allocator contract.

Cross-venue Spot candidates declare the Spot markets consumed on both venues. Positive-funding carry candidates declare the Spot and Perpetual markets they consume on Binance. Because resource keys are shared across families, the allocator can reject a funding candidate when a higher-priority cross-venue candidate already consumes the same Binance Spot market.

The funding adapter uses a conservative default capital multiplier rather than assuming free leverage. This bus is an allocation layer only: it does not submit orders. The standalone funding settlement replay now measures holding-period economics, but a stateful funding position engine is still required before unified shadow portfolio PnL can include it.

## Foreground public shadow operation

`novaarb-shadow-run` wires the existing Binance and Bybit public adapters into `MultiInstrumentPublicShadowScanner` and `ShadowLiveCoordinator`. It runs in the foreground, accepts hypothetical pre-funded balances, batches simultaneous signals for shared-capital allocation, reprices selected opportunities from current public books, applies only hypothetical inventory changes and emits optional heartbeat metrics through the research recorder.

Both public adapters share one `StreamTelemetry` collector in this command. The optional metrics log records heartbeat/final snapshots with transport reliability evidence alongside portfolio/risk evidence.

The runner has no authenticated exchange client. Running it for days or weeks is an operator/deployment action; the existence of the runner is not evidence that a multi-day profitability benchmark has already been completed.

## Shadow risk layer

The multi-asset ledger stores balances by `(venue, asset)`. A cross-venue shadow fill moves quote and base balances on the two venues while keeping unrelated assets untouched, which allows BTC, ETH and later instruments to share the same pre-funded inventory model.

The ledger can mark every nonzero asset into a common quote currency using observed reference prices. `ShadowRiskGuard` then evaluates total marked value, maximum venue concentration, maximum asset concentration, drift from configured venue target weights and conservative realized daily PnL. An unhealthy-data flag has the highest-priority kill switch.

## Capital allocation layer

The research allocator consumes normalized candidate economics rather than strategy-specific objects. It can enforce total deployable capital and cash reserve, maximum positions, maximum capital per opportunity, per-strategy position/capital limits, minimum expected edge, and resource exclusivity so overlapping markets cannot be double-allocated silently.

The v0.11 research stack can normalize cross-venue Spot and projected funding carry into the same candidate bus, and it can independently replay captured funding entries through observed settlements/exits. Remaining strategy families still need candidate adapters, and funding still requires a stateful lifecycle inside the unified portfolio before shadow PnL can mix the strategies on one timeline.

## Quantitative shadow acceptance gate

`evaluate_shadow_acceptance` consumes machine-readable shadow metrics plus an explicit observation duration. The default gate currently requires:

- at least 168 observation hours
- at least 250 detected signals
- at least 50 executed shadow trades
- at least 20% selected-to-executed survival
- at least 50% profitable shadow trades
- positive conservative net PnL
- no more than 3% marked drawdown relative to ending value
- no more than 5% risk-halt rate
- no more than 5% inventory-rejection rate
- zero unhealthy venue streams in supplied health evidence

The CLI `novaarb-shadow-gate` exits non-zero when the evidence fails. Thresholds are configurable because deployment capital, fee tier and venue mix can differ, but changing a threshold should be a deliberate research decision rather than a way to rescue a failed run.

Passing the gate is **necessary evidence for later engineering review, not a profit guarantee and not permission to enable live trading**.

## Safety boundary

There is no authenticated exchange client, order endpoint, withdrawal endpoint or transfer executor in v0.11. All Binance/Bybit shadow integration is public market data. Inventory, shadow-risk, funding-settlement and rebalancing components are simulations used to expose capital constraints and hidden execution costs.

Live execution remains a separate future milestone behind reviewed interfaces, hard exposure limits, kill switches, realized-strategy accounting and sustained shadow evidence.

## Evidence required before live execution

- multi-day and multi-week raw market capture from every venue used
- venue-specific latency p50/p95/p99 from the actual deployment environment
- cross-venue clock-skew and stale-feed distributions
- opportunity lifetime and edge-decay distributions
- survival curves by symbol/direction
- paper results after actual account fee-tier assumptions
- fee/rebalance sensitivity that remains acceptable outside one favorable assumption set
- inventory utilization, drift and rebalance-cost distributions
- funding settlement/exit replay across enough real captured settlements and regimes
- adverse selection after signal
- recovery frequency and recovery-loss distribution
- route/symbol/regime breakdowns
- venue disconnect/reconnect, queue-drop, rate-limit and data-quality evidence
- global exposure and kill-switch tests
- long-running multi-instrument shadow run with no real orders across multiple market regimes
- quantitative acceptance thresholds that pass before authenticated execution code is introduced
