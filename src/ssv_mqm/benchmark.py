"""Benchmark comparison rules (pure — no DB, network, or clocks).

Compares one published metric value against its configured target and classifies the
market as over- or under-performing. This is the canonical rule; the ``benchmark_comparison``
SQL view (migrations/002_benchmarks.sql) mirrors it for Grafana, the same way the
``aggregate_day`` SQL mirrors :mod:`ssv_mqm.aggregate`.

Direction depends on the metric: spread is a *max* target (lower is better), depth targets
are *mins* (higher is better). A metric with no target is not evaluated (``met``/``status``
are ``None``) — never a failure.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BenchmarkResult:
    """Outcome of comparing one actual value against its target.

    ``delta`` is the signed actual - target. ``met`` is True when the target is satisfied
    (or there is no target). ``status`` is ``"over"`` when the market beats its target,
    ``"under"`` when it misses, and ``None`` when no target is configured.
    """

    target: float | None
    delta: float | None
    met: bool | None
    status: str | None


def evaluate_benchmark(
    actual: float, target: float | None, *, lower_is_better: bool
) -> BenchmarkResult:
    """Classify ``actual`` against ``target``.

    ``lower_is_better=True`` for spread (target is a ceiling); ``False`` for depth (target
    is a floor). Exact equality counts as meeting the target (overperform).
    """
    if target is None:
        return BenchmarkResult(target=None, delta=None, met=None, status=None)
    delta = actual - target
    met = actual <= target if lower_is_better else actual >= target
    return BenchmarkResult(
        target=target,
        delta=delta,
        met=met,
        status="over" if met else "under",
    )
