# NovaArb architecture

## Prime invariant

A visible price difference is never treated as profit. Every candidate must survive this decomposition:

`mid-market dislocation - spread - depth slippage - fees - latency reserve - funding reserve = executable net edge`

The engine uses bid/ask depth, never last-traded price, for executable decisions.

## Phase 1 data flow

1. Binance public Spot and USD-M Perpetual partial depth streams (20 levels / 100 ms).
2. Normalized immutable `OrderBookSnapshot` objects.
3. `SpotPerpStrategy` constructs equal-base-quantity two-leg candidates.
4. `ExecutableEdgeModel` decomposes all modeled costs without double counting.
5. `RiskEngine` rejects stale, oversized, negative or too-small-edge candidates.
6. All evaluated candidates can be persisted as JSONL for later replay/research.
7. Approved candidates are displayed only. **No order API exists in phase 1.**

## Safety boundary

The initial codebase has no authenticated exchange client and no withdrawal or order endpoint. Public WebSocket market data is the only exchange integration. Live execution must be introduced later behind a separately reviewed interface after paper evidence exists.

## Planned engines

- Spot / perpetual dislocation (phase 1)
- Triangular arbitrage
- Quote/stablecoin synthetic paths (USDT/USDC/FDUSD)
- Funding/basis carry
- Cross-exchange pre-funded arbitrage
- Lead/lag research model (not classified as risk-free arbitrage)

## Research requirements before live execution

- event replay with recorded books
- fill/partial-fill simulation
- opportunity lifetime distribution
- fees by actual account tier
- latency histograms, p50/p95/p99
- adverse selection after signal
- per-symbol and per-regime statistics
- kill-switch and exposure invariants
- shadow/paper run across multiple weeks
