"""Custom trigger thresholds estimated from supplied source levels.

Mirrors hazardCustomTriggerLevel in hazard_trigger.js so the API and the map give
the same numbers. Between two source levels the exceedance probability is
interpolated linearly in log-probability (linearly when a bracket touches zero).
Beyond the supplied range the nearest segment's log-probability slope is extended,
an exponential-tail assumption. Where the source gives no decreasing slope, the
edge frequency is returned as a bound instead of an estimate. Lower and upper
frequencies of duplicate rounded levels are estimated separately.

Observed-record curves (nearby reported fires) pass extrapolate=False: past the
record the edge frequency is returned as a bound and no slope is extended.
"""
import math


def _log_linear(x0, y0, x1, y1, x):
    if x1 == x0:
        return y0
    weight = (x - x0) / (x1 - x0)
    if y0 > 0 and y1 > 0:
        return math.exp(math.log(y0) + weight * math.log(y1 / y0))
    return y0 + weight * (y1 - y0)


def _tail(levels, key, severity):
    lowest, highest = levels[0], levels[-1]
    if severity < lowest["severity"]:
        following = levels[1]
        if not lowest[key] > following[key] or following[key] <= 0:
            return lowest[key], "lower"
        return min(100.0, _log_linear(lowest["severity"], lowest[key], following["severity"], following[key], severity)), None
    positive = [level for level in levels if level[key] > 0]
    if highest[key] <= 0 or len(positive) < 2:
        return highest[key], "upper"
    a, b = positive[-2], positive[-1]
    if not a[key] > b[key]:
        return b[key], "upper"
    return _log_linear(a["severity"], a[key], b["severity"], b[key], severity), None


def custom_trigger_level(levels, severity, extrapolate=True):
    """Estimate {minimum, maximum} exceedance (%) at severity from sorted levels.

    levels: [{"severity", "minimum", "maximum"}] sorted by severity, unique severities.
    Returns a dict with "error" when no estimate can be made.
    """
    if not levels:
        return {"error": "No source levels are available to estimate from."}
    if isinstance(severity, bool) or not isinstance(severity, (int, float)) or not math.isfinite(severity) or severity < 0:
        return {"error": "Enter a non-negative threshold."}
    for level in levels:
        if level["severity"] == severity:
            return {**level, "method": "source", "direction": None, "bound": None, "basis": [level["severity"]], "reach": None}
    lowest, highest = levels[0], levels[-1]
    if lowest["severity"] < severity < highest["severity"]:
        upper = next(i for i, level in enumerate(levels) if level["severity"] > severity)
        a, b = levels[upper - 1], levels[upper]
        values = [min(100.0, max(0.0, _log_linear(a["severity"], a[k], b["severity"], b[k], severity))) for k in ("minimum", "maximum")]
        return {"severity": severity, "minimum": min(values), "maximum": max(values), "method": "interpolated",
                "direction": None, "bound": None, "basis": [a["severity"], b["severity"]], "reach": None}
    if not extrapolate:
        below = severity < lowest["severity"]
        edge = lowest if below else highest
        return {"severity": severity, "minimum": edge["minimum"], "maximum": edge["maximum"], "method": "bounded",
                "direction": "below" if below else "above", "bound": "lower" if below else "upper",
                "basis": [edge["severity"]], "reach": None}
    if len(levels) < 2:
        return {"error": "Only one source level is available, so there is no slope to extrapolate from."}
    (low, low_bound), (high, high_bound) = _tail(levels, "minimum", severity), _tail(levels, "maximum", severity)
    below = severity < lowest["severity"]
    edge, neighbour = (lowest, levels[1]) if below else (highest, levels[-2])
    span = abs(edge["severity"] - neighbour["severity"])
    return {"severity": severity, "minimum": max(0.0, min(low, high)), "maximum": min(100.0, max(low, high)),
            "method": "extrapolated", "direction": "below" if below else "above", "bound": low_bound or high_bound,
            "basis": sorted([neighbour["severity"], edge["severity"]]),
            # How far past the data, in widths of the segment the slope came from.
            "reach": abs(severity - edge["severity"]) / span if span > 0 else None}


def _fmt(value):
    return f"{value:.6g}"


def custom_trigger_note(level, unit):
    unit = f" {unit}" if unit else ""
    if level["method"] == "source":
        return "This threshold matches a supplied source level, so the source value is used directly."
    a, b = (f"{_fmt(v)}{unit}" for v in (level["basis"][0], level["basis"][-1]))
    if level["method"] == "bounded":
        if level["bound"] == "upper":
            return (f"Above the highest observed level ({a}) the record has no years to count, and nothing is extrapolated. "
                    f"A larger threshold cannot be met more often, so the {a} frequency is shown as an upper bound, not an estimate. "
                    "Zero observed years does not mean zero future risk.")
        return (f"Below the lowest supplied level ({a}) nothing is extrapolated; a smaller threshold is met at least as often, "
                f"so the {a} frequency is shown as a lower bound, not an estimate.")
    if level["method"] == "interpolated":
        return f"Estimated by log-probability interpolation between the supplied {a} and {b} levels. This is not a source value."
    if level["bound"] == "upper" and level["maximum"] == 0:
        return (f"The highest supplied level ({b}) already has a 0% frequency, and exceedance cannot rise with severity, "
                "so 0% is shown. A zero in the source does not establish zero future risk.")
    if level["bound"] == "upper":
        return f"Beyond the highest supplied level ({b}) the source gives no decreasing slope, so the {b} frequency is shown as an upper bound, not an estimate."
    if level["bound"] == "lower":
        return f"Below the lowest supplied level ({a}) the source gives no slope, so the {a} frequency is shown as a lower bound, not an estimate."
    where = "above the highest" if level["direction"] == "above" else "below the lowest"
    note = (f"Extrapolated {where} supplied level by extending the log-probability slope between {a} and {b} "
            "(an exponential-tail assumption). Real tails may be heavier or lighter.")
    if level["reach"] is not None and level["reach"] > 1:
        note += f" This is {level['reach']:.1f}× the width of the fitted segment past the data, so treat it as highly uncertain."
    return note
