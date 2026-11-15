// Trigger choices come only from supplied source levels. No interpolation or
// extrapolation: missing depths and historical zeros retain their meaning.
function hazardTriggerLevels(data) {
  if (data?.status !== "available" || data.annual_probability_available === false ||
      data.peril === "fireline_intensity" || !Array.isArray(data.points)) return [];
  const levels = new Map();
  for (const point of data.points) {
    if (!Number.isFinite(point.severity) || point.severity < 0 ||
        !Number.isFinite(point.frequency_pct) || point.frequency_pct < 0 || point.frequency_pct > 100) continue;
    const level = levels.get(point.severity) || {
      severity: point.severity, minimum: point.frequency_pct, maximum: point.frequency_pct,
    };
    // Rounded source levels can coincide. Keep the supplied probability range
    // instead of silently choosing one of the conflicting frequencies.
    level.minimum = Math.min(level.minimum, point.frequency_pct);
    level.maximum = Math.max(level.maximum, point.frequency_pct);
    levels.set(point.severity, level);
  }
  return [...levels.values()].sort((a, b) => a.severity - b.severity);
}

function hazardTriggerBasis(peril) {
  if (peril === "potential_fire_intensity") return {
    label: "Annual potential-intensity exceedance frequency",
    note: "Share of complete 1991–2020 years with modeled hourly potential surface-fire intensity at or above this level on a fixed landscape. This is a weather-condition trigger, not the chance of an actual wildfire or property damage. Contributions illustrate that potential-condition trigger only.",
  };
  if (peril === "fireline_intensity") return {
    label: "Annual exceedance probability unavailable",
    note: "Native weather-weighted intensity is not an annual probability. No annual contribution can be calculated from this source.",
  };
  if (peril === "heat") return {
    label: "Observed annual heat exceedance frequency",
    note: "Share of included complete 1991–2020 years with a daily maximum air temperature at or above this level at the selected historical station. This station proxy is not a calibrated future probability, a multi-day heat-wave metric or a construction exposure limit.",
  };
  if (peril === "nearest_fire") return {
    label: "Observed annual reported-fire frequency",
    note: "Share of published 1992–2024 archive years with at least one USFS FPA FOD reported wildfire of at least this final size whose reported point lies within the selected radius. This is a historical reporting frequency, not a calibrated future probability or the chance that this property burns. Sizes never reported within the radius are not offered: zero observed years does not mean zero future risk.",
  };
  if (peril === "cyclone") return {
    label: "Observed annual exceedance frequency",
    note: "Share of complete historical years with a sampled wind footprint at this level. This is not a calibrated future probability. Zero observed years does not mean zero future risk.",
  };
  if (peril === "wildfire") return {
    label: "Estimated annual exceedance probability",
    note: "One-year research estimate from annual burn probability × conditional flame-length probability. A modeled zero does not establish no fire or ember exposure.",
  };
  return {
    label: "Annual exceedance probability",
    note: "Modeled chance of meeting or exceeding this level in one year at the selected location. A source value is used directly; no interpolation or tail extrapolation is applied.",
  };
}

// Source probabilities are percentages; payout is a fixed amount paid once
// if the selected trigger is met or exceeded during the year.
function hazardPoolContribution(level, payout) {
  if (!level || !Number.isFinite(payout) || payout < 0 ||
      !Number.isFinite(level.minimum) || !Number.isFinite(level.maximum) ||
      level.minimum < 0 || level.maximum > 100 || level.minimum > level.maximum) return null;
  return {
    minimum: (level.minimum / 100) * payout,
    maximum: (level.maximum / 100) * payout,
  };
}

if (typeof module !== "undefined") module.exports = { hazardTriggerLevels, hazardTriggerBasis, hazardPoolContribution };
