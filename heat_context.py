"""Observation-derived heat screening, separate from annual hazard models.

Methods and the deliberately bounded display envelope: docs/heat-and-wind.md.
"""
from datetime import datetime
import math

EQUATION_URL = "https://www.wpc.ncep.noaa.gov/html/heatindex_equation.shtml"
HUMIDITY_URL = "https://www.weather.gov/media/epz/wxcalc/vaporPressure.pdf"
BANDS_URL = "https://www.weather.gov/ama/heatindex"
CHART_URL = "https://www.noaa.gov/sites/default/files/2022-05/heatindex_chart_rh.pdf"
MAX_AGE_SECONDS = 7200  # Existing station-context freshness policy, not a safety limit.


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def relative_humidity(temperature_c, dew_point_c):
    """NWS vapor-pressure ratio over water; both inputs must be degrees C."""
    if (not finite_number(temperature_c) or not finite_number(dew_point_c)
            or not -80 <= dew_point_c <= temperature_c <= 60):
        return None
    return 100 * 10 ** (7.5 * dew_point_c / (237.3 + dew_point_c)
                       - 7.5 * temperature_c / (237.3 + temperature_c))


def nws_heat_index_f(temperature_f, humidity_pct):
    """WPC simple screening formula, Rothfusz regression and RH adjustments.

    Caller enforces the display envelope. Do not apply the regression alone to
    cool conditions, or treat these apparent temperatures as measured WBGT.
    """
    t, rh = temperature_f, humidity_pct
    simple = 0.5 * (t + 61 + (t - 68) * 1.2 + rh * 0.094)
    preliminary = (simple + t) / 2
    if preliminary < 80:
        return preliminary
    result = (-42.379 + 2.04901523*t + 10.14333127*rh - 0.22475541*t*rh
              - 0.00683783*t*t - 0.05481717*rh*rh + 0.00122874*t*t*rh
              + 0.00085282*t*rh*rh - 0.00000199*t*t*rh*rh)
    if rh < 13 and 80 <= t <= 112:
        result -= (13-rh)/4 * math.sqrt((17-abs(t-95))/17)
    elif rh > 85 and 80 <= t <= 87:
        result += (rh-85)/10 * (87-t)/5
    return result


def heat_index_band(value_f):
    """NWS Amarillo bands; use unrounded HI and half-open boundary intervals."""
    if value_f >= 125:
        return "Extreme danger"
    if value_f >= 103:
        return "Danger"
    if value_f >= 90:
        return "Extreme caution"
    if value_f >= 80:
        return "Caution"
    return "Below listed caution bands; heat illness is still possible"


def heat_context(observation, now):
    """Retain valid station temperatures, but withhold HI for unusable evidence."""
    result = {
        "status": "unavailable", "data_kind": "observation_derived",
        "metric": "Heat Index (shade)", "air_temperature_c": None,
        "dew_point_c": None, "relative_humidity_pct": None,
        "heat_index_c": None, "heat_index_f": None, "category": None,
        "observed_at": observation.get("observed_at"),
        "method": "NWS vapor-pressure RH estimate; WPC Heat Index algorithm",
        "source_urls": [EQUATION_URL, HUMIDITY_URL, BANDS_URL, CHART_URL],
        "reason": "No usable timestamped station temperature/dew-point report.",
    }
    # Never interpret a missing value as zero or combine different reports.
    values = {v.get("variable"): v.get("value") for v in observation.get("values", [])
              if v.get("unit") == "°C"}
    temperature, dew_point = values.get("Air temperature"), values.get("Dew point")
    if finite_number(temperature) and -80 <= temperature <= 60:
        result["air_temperature_c"] = temperature
    if finite_number(dew_point) and -80 <= dew_point <= 60:
        result["dew_point_c"] = dew_point
    try:
        instant = datetime.fromisoformat(result["observed_at"].replace("Z", "+00:00"))
        if instant.tzinfo is None:
            raise ValueError("UTC offset required")
        age = now - instant.timestamp()
    except (AttributeError, TypeError, ValueError, OverflowError, OSError):
        return result
    if age < 0:
        result["reason"] = "Report timestamp is in the future; Heat Index withheld."
        return result
    if age > MAX_AGE_SECONDS or observation.get("status") == "stale":
        result["reason"] = "Report is older than 2 hours; Heat Index withheld."
        return result
    if observation.get("status") != "available":
        return result
    rh = relative_humidity(temperature, dew_point)
    if rh is None:
        result["reason"] = "Valid temperature and dew point from the same report are required; dew point must not exceed temperature."
        return result
    result["relative_humidity_pct"] = rh
    t_f = temperature * 9 / 5 + 32
    # Conservative product display guard using the extent of NOAA's expanded
    # chart. This is not an occupational exposure threshold or validation claim.
    if not 76 <= t_f <= 126 or not 4 <= rh <= 100:
        result["reason"] = "Outside this feature's display envelope (air 76–126 °F, RH 4–100%); Heat Index withheld. This does not imply low heat hazard."
        return result
    hi = nws_heat_index_f(t_f, rh)
    if not math.isfinite(hi) or hi > 183:
        result["reason"] = "Beyond this feature's NOAA chart display envelope (Heat Index above 183 °F); value withheld. This does not imply low heat hazard."
        return result
    result.update(status="available", heat_index_f=hi, heat_index_c=(hi-32)*5/9,
                  category=heat_index_band(hi),
                  reason="Derived screening estimate for shade/light wind at this station and report time. Not WBGT, a forecast, a heat-wave assessment, or a work/stop decision.")
    return result
