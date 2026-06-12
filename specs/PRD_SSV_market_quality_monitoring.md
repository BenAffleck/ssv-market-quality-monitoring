# PRD — SSV Token Market-Quality Monitoring (DIY via CCXT Pro)

**Status:** Draft v1 for review
**Owner:** Data Analytics
**Last updated:** 2026-06-10

## Summary

An in-house system that continuously collects public order-book data for the **SSV token** from five centralized exchanges (Binance, Bybit, Gate.io, KuCoin, OKX) using **CCXT Pro** WebSocket feeds, computes per-sample **spread** and **depth** (within ±100 bps and ±200 bps of mid), aggregates these to **daily averages per exchange**, and surfaces them in an internal **dashboard**. The "DIY" approach is deliberate: public order-book data on all five venues is free, so this is an infrastructure-and-reliability build, not a data-licensing purchase.

**Key assumptions** (carried from the brief; see Open Questions for those still to confirm):
- Scope is **spot SSV/USDT and spot SSV/USDC** on the five named exchanges. USDT/USDC is treated as ≈ USD for depth notional.
- Output is **daily** averages — this is a market-quality monitoring tool, not a real-time/HFT system.
- Users are **internal** to the client (market operations, analytics, leadership). No external/public surface in v1.
- We start collecting forward from go-live; **no pre-launch historical backfill** in v1.

---

## Problem Statement

The client lists SSV on multiple centralized exchanges but has **no objective, continuous, comparable view of market quality** (spread tightness and book depth) across those venues. Today, checking liquidity health is manual and venue-by-venue: exchange UIs aren't comparable, aren't historized, and don't compute depth within defined price bands. As a result, the team can't reliably tell when liquidity degrades on a venue, can't hold market-maker partners to a shared standard, and can't make evidence-based decisions about where to focus liquidity incentives or whether a listing remains healthy. Paid aggregators solve this but add recurring cost and aren't tailored to a single token — and the underlying data is free to collect directly.

---

## Goals

1. **Detect liquidity degradation within one day.** A material widening of spread or thinning of depth on any venue is visible in the dashboard the next day.
2. **Establish a single, auditable source of truth** for SSV spread and depth that internal teams — and market-maker partners — accept as authoritative, because the methodology is documented and reproducible.
3. **Eliminate manual liquidity reporting.** Replace ad-hoc, per-venue manual pulls with an automated daily metric.
4. **Validate the build-vs-buy decision by operating at minimal cost** — keep recurring data + infrastructure spend low enough that DIY is clearly cheaper than a managed feed for this single-token scope.

---

## Non-Goals

1. **Not a trading or execution system.** Read-only market data only. No order placement, no funds movement, no trade-scoped API keys. (Scope + security; also removes the highest-risk failure modes.)
2. **Not real-time / HFT-grade.** The deliverable is a *daily average*. We will not build co-located, microsecond-latency infrastructure or treat sub-second freshness as a requirement. (Avoids over-engineering a monitoring tool.)
3. **Not historical backfill (v1).** We collect from go-live forward. Ingesting pre-launch history (e.g., via a paid provider) is deferred. (Separate initiative; see P2.)
4. **Not derivatives/perpetuals (v1).** Spot SSV/USDT and SSV/USDC only, even though perp volume is larger. (Flagged as an open scope question; see P2.)
5. **Not DEX / on-chain liquidity.** Centralized exchanges only, per the requirement. (Different data model and methodology.)
6. **Not a multi-token platform (v1).** SSV only. The architecture should *not preclude* adding tokens later, but generalization is not built now. (Premature; see P2.)
7. **Not externally shared (v1).** No partner- or public-facing view and no external access controls in v1. (Internal validation first; see P2.)

---

## User Stories

### Market Operations Manager (primary)
- As a market-ops manager, I want to see the **average daily spread** for SSV on each exchange so I can tell which venues offer the tightest markets.
- As a market-ops manager, I want to see **±1% and ±2% depth (USD)** per exchange per day so I can judge how much size each book can absorb without significant slippage.
- As a market-ops manager, I want to **compare venues side by side and over time** so I can decide where to direct liquidity incentives or escalate to a market maker.
- As a market-ops manager, I want to be **notified when spread widens or depth thins past a threshold** on any venue so I can act the same day. *(P1)*

### Data Analyst (builder / maintainer)
- As the analyst, I want a **deterministic, documented metric definition** so the numbers are auditable and reproducible.
- As the analyst, I want each daily figure to carry a **data-coverage indicator** (how many samples it's based on) so I never present a misleading average after a partial outage.
- As the analyst, I want collectors to **auto-reconnect and resync** after a disconnect so the pipeline doesn't need babysitting.
- As the analyst, I want to **recompute past days if the methodology changes**, which requires retaining sufficient underlying data. *(raw-snapshot retention is P1)*

### Leadership / Exec Stakeholder
- As a leadership stakeholder, I want a **glanceable summary of SSV liquidity health and its trend** so I can understand the token's market standing without digging into the detail.

### Edge / boundary cases the system must handle
- An exchange WebSocket disconnects mid-day, or a sequence gap is detected → resync without corrupting the book or the day's average.
- The order book is **thin within a band** (few or zero levels inside ±1%) → depth reported correctly (including legitimately low/zero), not as an error.
- A **locked/crossed book** snapshot (best bid ≥ best ask) → sample flagged/excluded rather than producing a negative spread.
- A day has **too few samples** (e.g., long outage) → flagged or excluded from trend lines, not silently averaged.
- The **symbol is delisted or renamed** on a venue → collector fails gracefully and alerts, rather than emitting empty data as if healthy.

---

## Requirements

### Must-Have (P0) — the minimum viable system

**P0-1. Order-book ingestion via CCXT Pro (WebSocket, snapshot + incremental).**
Maintain a live local order book per venue for SSV/ and SSV/USDC using CCXT Pro's unified `watchOrderBook` interface (snapshot + diff updates), the recommended reliable pattern on all five exchanges.
- [ ] Live local book maintained for SSV/USDT and SSV/USDC on Binance, Bybit, Gate.io, KuCoin, OKX.
- [ ] Book depth retrieved is deep enough to always cover the ±2% band (the band is narrow for SSV, so deep-enough L2 — not full L3 — is sufficient).
- [ ] KuCoin connection token obtained automatically (public token, no auth) and refreshed as needed.
- [ ] Any credential used (e.g., KuCoin) is **read-only / market-data scope only** (see P0-8).

**P0-2. Per-sample metric computation.**
At a fixed cadence, compute and persist per-sample metrics from the current book.
- Given a valid book snapshot with `best_bid` and `best_ask`,
- When a sample is taken,
- Then compute `mid = (best_ask + best_bid)/2`, `spread = (best_ask − best_bid)/mid`, and depth as the summed USD notional (`Σ price×size`) of bids ≥ `mid×0.99` and asks ≤ `mid×1.01` (±100 bps), and bids ≥ `mid×0.98` and asks ≤ `mid×1.02` (±200 bps).
- [ ] Depth stored as bid-side and ask-side components (so `Depth = bid + ask` is reproducible).
- [ ] Sampling cadence is configurable (default 5s — see Open Questions).
- [ ] Crossed/locked samples (best_bid ≥ best_ask) are flagged and excluded from spread averaging.

**P0-3. Daily aggregation per exchange.**
- Given all valid samples for a UTC day on an exchange,
- When the daily job runs,
- Then output average spread (expressed as a **percentage rounded to two decimals**) and average ±100 bps and ±200 bps depth (USD), per exchange per day.
- [ ] Day boundary is UTC (see Open Questions if another boundary is required).
- [ ] Averaging method is **simple mean over fixed-cadence samples** in v1 (time-weighted is P1).

**P0-4. Persistent storage.**
- [ ] Per-sample computed metrics retained in a time-series-capable store (e.g., TimescaleDB/Postgres, ClickHouse, or partitioned Parquet).
- [ ] Daily aggregates retained and queryable for trend display.
- [ ] Schema includes exchange, symbol, timestamp, and all metric components.

**P0-5. Dashboard.**
- [ ] Per exchange and per day: average spread %, depth ±100 bps (USD), depth ±200 bps (USD).
- [ ] Time-series view per metric and a cross-exchange comparison view.
- [ ] Each daily data point exposes its coverage indicator (from P0-6).
- [ ] Built on an existing tool where possible (e.g., Grafana or Metabase over the store) rather than a bespoke front end.

**P0-6. Data-quality / coverage handling.** *(genuinely P0 — protects the source-of-truth goal)*
- [ ] Each daily aggregate records `samples_captured / samples_expected` (coverage %).
- [ ] Days below a coverage threshold (e.g., <90%) are visually flagged and excluded from trend/comparison aggregations by default.
- [ ] Gaps and excluded samples are logged for the analyst.

**P0-7. Reliability: reconnect + resync.**
- Given a WebSocket disconnect or a detected sequence/update-ID gap,
- When the condition occurs,
- Then the collector reconnects, re-snapshots, and resumes without producing corrupted depth values, per each exchange's documented continuity rules.
- [ ] Automatic reconnection with backoff.
- [ ] Per-exchange sequence-gap detection triggers a fresh snapshot.
- [ ] Collectors run under a process supervisor (auto-restart on crash).

**P0-8. Read-only security posture.**
- [ ] No trade-enabled or withdrawal-enabled API keys anywhere in the system.
- [ ] Only public market-data feeds used; the single KuCoin key (if needed for full-depth REST resync) is market-data scope only.
- [ ] No secrets in source control; keys stored in a secrets manager / environment.

---

## Success Metrics

### Leading indicators (days–weeks)
- **Data coverage:** ≥ 99% of expected samples captured per exchange per day (stretch: 99.5%). *Measured as `samples_captured/samples_expected` in the aggregation job.*
- **Collector uptime:** ≥ 99.5%. *Measured from the process supervisor / health checks.*
- **Dashboard freshness:** previous UTC day's aggregates available by 01:00 UTC daily. *Measured from aggregation-job completion timestamp.*
- **Adoption:** market-ops opens the dashboard ≥ once per business day within the first month.

### Lagging indicators (weeks–months)
- **Manual effort eliminated:** liquidity reporting time goes from its current weekly baseline to ≈ 0 hours. *(Baseline to be captured before launch.)*
- **Decisions informed:** number of liquidity actions (MM escalations, incentive adjustments, listing reviews) attributed to the dashboard per quarter — target ≥ a handful in the first quarter, indicating real use.
- **Cost discipline:** total recurring data + infrastructure spend stays under the agreed cap (target < $200/month), confirming the DIY decision. *Measured from cloud billing.*
- **Source-of-truth acceptance (qualitative milestone):** at least one market-maker partner accepts the dashboard's methodology as the shared reference.