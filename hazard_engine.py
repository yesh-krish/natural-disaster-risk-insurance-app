"""NDApp research engine: occurrence probability, annual-maximum pricing, triggers.

No external packages or money movement. Providers must supply calibrated location
models; observations must be authenticated and normalized by a trusted ingest worker.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Mapping, Protocol, Sequence


UNITS = {"pga_pct_g": "%g", "rain_24h_mm": "mm", "wind_gust_kn": "kn",
         "inundation_m": "m", "flame_length_m": "m"}


def finite(value: float, name: str, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"Invalid {name}")
    return float(value)


def coordinates(lat: float, lon: float) -> None:
    finite(lat, "latitude", -90)
    finite(lon, "longitude", -180)
    if lat > 90 or lon > 180:
        raise ValueError("Coordinates out of range")


def rate_to_aep(rate: float, years: float = 1) -> float:
    return -math.expm1(-finite(rate, "annual rate") * finite(years, "years"))


def aep_to_rate(aep: float) -> float:
    aep = finite(aep, "AEP")
    if aep >= 1:
        raise ValueError("Equivalent Poisson rate requires AEP < 1")
    return -math.log1p(-aep)


class ModelUnavailable(ValueError):
    """Missing coverage, unsupported threshold, or incomplete model."""


class LocationModel(Protocol):
    def aep(self, thresholds: Mapping[str, float], operator: str = "AND") -> float: ...


@dataclass(frozen=True)
class HazardCurve:
    metric: str
    # Increasing intensity, decreasing annual rate OR annual probability.
    points: tuple[tuple[float, float], ...]
    frequency_measure: str
    interpolation: str = "log_frequency_linear_intensity"

    def __post_init__(self):
        if self.metric not in UNITS or len(self.points) < 2:
            raise ValueError("Curve needs a recognized metric and at least two points")
        if self.frequency_measure not in {"annual_rate", "annual_probability"}:
            raise ValueError("Specify annual_rate or annual_probability")
        if self.interpolation not in {"log_log", "log_frequency_linear_intensity"}:
            raise ValueError("Unsupported interpolation")
        previous_x, previous_y = -math.inf, math.inf
        for x, y in self.points:
            finite(x, "intensity")
            finite(y, "frequency")
            if x <= previous_x or y > previous_y:
                raise ValueError("Curve must increase in intensity and decrease in frequency")
            if self.frequency_measure == "annual_probability" and y > 1:
                raise ValueError("AEP must be <= 1")
            if self.interpolation == "log_log" and x <= 0:
                raise ValueError("log_log requires positive intensities")
            previous_x, previous_y = x, y

    def frequency(self, threshold: float) -> float:
        finite(threshold, "threshold")
        if not self.points[0][0] <= threshold <= self.points[-1][0]:
            raise ModelUnavailable("Threshold outside curve; tail extrapolation is disabled")
        for x, y in self.points:
            if threshold == x:
                return y
        for (x0, y0), (x1, y1) in zip(self.points, self.points[1:]):
            if x0 < threshold < x1:
                if y0 <= 0 or y1 <= 0:
                    raise ModelUnavailable("Cannot interpolate a zero/underflow tail")
                if self.interpolation == "log_log":
                    weight = math.log(threshold / x0) / math.log(x1 / x0)
                else:
                    weight = (threshold - x0) / (x1 - x0)
                return math.exp(math.log(y0) + weight * math.log(y1 / y0))
        raise ModelUnavailable("No threshold bracket")

    def aep(self, thresholds: Mapping[str, float], operator: str = "AND") -> float:
        if operator not in {"AND", "OR"} or set(thresholds) != {self.metric}:
            raise ValueError("Threshold metric does not match curve")
        y = self.frequency(thresholds[self.metric])
        return rate_to_aep(y) if self.frequency_measure == "annual_rate" else y


@dataclass(frozen=True)
class StormEvent:
    event_id: str
    rain_24h_mm: float
    wind_gust_kn: float
    annual_rate: float

    def __post_init__(self):
        if not self.event_id:
            raise ValueError("Event identity is required")
        finite(self.rain_24h_mm, "rain")
        finite(self.wind_gust_kn, "wind")
        finite(self.annual_rate, "event rate")


@dataclass(frozen=True)
class StormCatalog:
    """Paired, declustered site-event maxima with annual occurrence weights.

    Empirical historical weights are 1 / complete_exposure_years. Synthetic
    catalogs use simulation weights. Zero historical hits cannot price a rare tail.
    """
    events: tuple[StormEvent, ...]
    model_kind: str = "empirical"

    def __post_init__(self):
        if not self.events or self.model_kind not in {"empirical", "synthetic"}:
            raise ValueError("Supply an empirical or synthetic storm catalog")
        if len({e.event_id for e in self.events}) != len(self.events):
            raise ValueError("Duplicate storm IDs would double count frequency")

    def aep(self, thresholds: Mapping[str, float], operator: str = "AND") -> float:
        if set(thresholds) != {"rain_24h_mm", "wind_gust_kn"}:
            raise ValueError("Storm thresholds require both rain and wind")
        if operator not in {"AND", "OR"}:
            raise ValueError("Unsupported operator")
        for value in thresholds.values():
            finite(value, "threshold")
        rate = 0.0
        for event in self.events:
            hits = (event.rain_24h_mm >= thresholds["rain_24h_mm"],
                    event.wind_gust_kn >= thresholds["wind_gust_kn"])
            if all(hits) if operator == "AND" else any(hits):
                rate += event.annual_rate
        if rate == 0:
            raise ModelUnavailable("No catalog tail support; zero hits are not zero risk")
        return rate_to_aep(rate)

    def surface(self, rain_axis: Sequence[float], wind_axis: Sequence[float]) -> list[dict]:
        output = []
        for rain in rain_axis:
            for wind in wind_axis:
                try:
                    p = self.aep({"rain_24h_mm": rain, "wind_gust_kn": wind})
                except ModelUnavailable:
                    p = None
                output.append({"thresholds": {"rain_24h_mm": rain, "wind_gust_kn": wind},
                               "annual_exceedance_probability": p})
        return output


def wildfire_curve(burn_probability: float,
                   conditional_flame_survival: Sequence[tuple[float, float]]) -> HazardCurve:
    """Annual maximum flame length: BP * P(L >= l | site burns in year).

    Conditional layer must describe the annual burn maximum, from the SAME model
    as BP. Do not feed mean flame length or unconditional class probabilities.
    """
    bp = finite(burn_probability, "burn probability")
    if bp > 1:
        raise ValueError("Burn probability must be <= 1")
    for _, survival in conditional_flame_survival:
        if finite(survival, "conditional survival") > 1:
            raise ValueError("Conditional survival must be <= 1")
    return HazardCurve("flame_length_m", tuple((x, bp * s) for x, s in conditional_flame_survival),
                       "annual_probability")


def daily_fire_probabilities(bp: float, scores: Sequence[float]) -> list[float]:
    """Allocate a baseline annual integrated hazard over one calibrated full year.

    scores = exp(beta * standardized FWI covariates + seasonal/spatial terms).
    A forecast-only slice is NOT a complete annual normalization population.
    """
    rate = aep_to_rate(bp)
    if not scores:
        raise ValueError("Provide a complete year's daily scores")
    values = [finite(s, "daily score") for s in scores]
    total = math.fsum(values)
    if total <= 0 or not math.isfinite(total):
        raise ValueError("Invalid score normalization")
    return [rate_to_aep(rate * s / total) for s in values]


@dataclass(frozen=True)
class Tier:
    thresholds: Mapping[str, float]
    cumulative_payout: float


def validate_tiers(tiers: Sequence[Tier], limit: float, operator: str) -> None:
    finite(limit, "annual limit")
    if not tiers or operator not in {"AND", "OR"}:
        raise ValueError("Provide tiers and AND/OR")
    previous = None
    payout = 0.0
    for tier in tiers:
        if not tier.thresholds or not set(tier.thresholds) <= UNITS.keys():
            raise ValueError("Unsupported threshold metrics")
        for value in tier.thresholds.values():
            finite(value, "threshold")
        amount = finite(tier.cumulative_payout, "payout")
        if amount <= payout or amount > limit:
            raise ValueError("Cumulative payouts must increase and fit the annual limit")
        if previous is not None:
            if set(previous) != set(tier.thresholds):
                raise ValueError("Tiers must use the same metrics")
            if any(tier.thresholds[k] < previous[k] for k in previous):
                raise ValueError("Tier exceedance regions must be nested")
        previous, payout = tier.thresholds, amount


def benchmark_quote(model: LocationModel, tiers: Sequence[Tier], annual_limit: float,
                    operator: str = "AND", fixed_expense: float = 0,
                    risk_load: float = 0, expense_ratio: float = 0) -> dict:
    """One-year annual-maximum/top-up contract, with explicit illustrative loads."""
    validate_tiers(tiers, annual_limit, operator)
    finite(fixed_expense, "fixed expense")
    finite(risk_load, "risk load")
    finite(expense_ratio, "expense ratio")
    if expense_ratio >= 1:
        raise ValueError("Expense ratio must be < 1")
    probabilities = [model.aep(t.thresholds, operator) for t in tiers]
    for i, p in enumerate(probabilities):
        if not math.isfinite(p) or not 0 <= p <= 1:
            raise ValueError("Model returned an invalid AEP")
        if i and p > probabilities[i - 1] + 1e-12:
            raise ValueError("Non-monotone tier probabilities")
    increments = [t.cumulative_payout - (tiers[i - 1].cumulative_payout if i else 0)
                  for i, t in enumerate(tiers)]
    expected = math.fsum(delta * p for delta, p in zip(increments, probabilities))
    premium = (expected * (1 + risk_load) + fixed_expense) / (1 - expense_ratio)
    return {"contract_basis": "one_year_annual_maximum_top_up", "tier_aep": probabilities,
            "expected_annual_payout": expected, "benchmark_premium": premium,
            "loads": {"risk_load": risk_load, "fixed_expense": fixed_expense,
                      "expense_ratio": expense_ratio}}


class ModelProvider(Protocol):
    def model_at(self, latitude: float, longitude: float) -> LocationModel: ...


def quote_at(latitude: float, longitude: float, provider: ModelProvider,
             tiers: Sequence[Tier], annual_limit: float, **loads) -> dict:
    coordinates(latitude, longitude)
    result = benchmark_quote(provider.model_at(latitude, longitude), tiers, annual_limit, **loads)
    return {"latitude": latitude, "longitude": longitude, **result}


@dataclass(frozen=True)
class Policy:
    policy_id: str
    location_id: str
    peril: str
    starts_at: datetime
    ends_at: datetime
    tiers: tuple[Tier, ...]
    annual_limit: float
    # Exact measurement definition (gust duration/height, rain interval, datum).
    definitions: Mapping[str, str]
    allowed_sources: Mapping[str, frozenset[str]]
    operator: str = "AND"

    def __post_init__(self):
        validate_tiers(self.tiers, self.annual_limit, self.operator)
        metrics = set(self.tiers[0].thresholds)
        allowed = {"earthquake": {"pga_pct_g"}, "rain_wind": {"rain_24h_mm", "wind_gust_kn"},
                   "flood": {"inundation_m"}, "wildfire": {"flame_length_m"}}
        if self.peril not in allowed or metrics != allowed[self.peril]:
            raise ValueError("Policy metrics must match peril")
        if not self.policy_id or not self.location_id:
            raise ValueError("Policy and location IDs are required")
        if set(self.definitions) != metrics or set(self.allowed_sources) != metrics:
            raise ValueError("Policy must pin definitions and approved sources")
        if any(not self.definitions[k] or not self.allowed_sources[k] for k in metrics):
            raise ValueError("Definitions and source allowlists cannot be empty")
        aware(self.starts_at)
        aware(self.ends_at)
        if self.starts_at >= self.ends_at:
            raise ValueError("Invalid policy term")


@dataclass(frozen=True)
class Observation:
    location_id: str
    peril: str
    event_id: str
    metric: str
    definition_id: str
    value: float | None
    unit: str
    window_start: datetime
    window_end: datetime
    issued_at: datetime
    source_id: str
    artifact_sha256: str
    quality: str
    finalized: bool
    revision: str


def aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Timezone-aware timestamps required")


def evaluate_trigger(policy: Policy, observations: Sequence[Observation], event_id: str,
                     now: datetime, already_paid: float = 0,
                     max_publication_age_seconds: float = 86400) -> dict:
    """Pure eligibility decision; caller must authenticate ingest and lock ledger.

    The same storm's independently timed maxima may match AND, per pinned event
    definition. Revisions/replays cannot increase paid amounts without a higher
    tier. Already-paid is the locked POLICY-YEAR total, not just event total.
    """
    aware(now)
    finite(already_paid, "already paid")
    finite(max_publication_age_seconds, "publication age")
    if already_paid > policy.annual_limit or not event_id:
        raise ValueError("Invalid ledger state or event ID")
    selected = {}
    for obs in observations:
        if obs.metric not in policy.definitions:
            continue
        if obs.metric in selected:
            return {"triggered": False, "status": "insufficient_data", "reason": "ambiguous revisions"}
        selected[obs.metric] = obs
    values = {}
    errors = []
    for metric in policy.definitions:
        obs = selected.get(metric)
        if obs is None:
            errors.append(f"{metric}: missing")
            continue
        try:
            for stamp in (obs.window_start, obs.window_end, obs.issued_at):
                aware(stamp)
            finite(obs.value, "observation")
        except (ValueError, TypeError):
            errors.append(f"{metric}: invalid value/time")
            continue
        age = (now - obs.issued_at).total_seconds()
        valid = (obs.location_id == policy.location_id and obs.peril == policy.peril
                 and obs.event_id == event_id and obs.definition_id == policy.definitions[metric]
                 and obs.source_id in policy.allowed_sources[metric] and obs.unit == UNITS[metric]
                 and obs.quality == "accepted" and obs.finalized and bool(obs.revision)
                 and len(obs.artifact_sha256) == 64
                 and all(c in "0123456789abcdef" for c in obs.artifact_sha256)
                 and policy.starts_at <= obs.window_start <= obs.window_end < policy.ends_at
                 and obs.window_end <= obs.issued_at <= now
                 and 0 <= age <= max_publication_age_seconds)
        if metric == "rain_24h_mm":
            valid = valid and (obs.window_end - obs.window_start).total_seconds() == 86400
        if not valid:
            errors.append(f"{metric}: source, definition, quality, or time mismatch")
        else:
            values[metric] = obs.value
    if errors:
        return {"triggered": False, "status": "insufficient_data", "reason": "; ".join(errors)}
    target = 0.0
    for tier in policy.tiers:
        hits = [values[k] >= threshold for k, threshold in tier.thresholds.items()]
        if all(hits) if policy.operator == "AND" else any(hits):
            target = tier.cumulative_payout
    due = max(0.0, min(target, policy.annual_limit) - already_paid)
    return {"triggered": due > 0, "status": "eligible" if due else "no_payment_due",
            "threshold_breached": target > 0,
            "event_id": event_id, "target_annual_payout": target, "payout_due": due,
            "evidence": [{"metric": o.metric, "source_id": o.source_id,
                          "revision": o.revision, "artifact_sha256": o.artifact_sha256}
                         for o in selected.values()]}
