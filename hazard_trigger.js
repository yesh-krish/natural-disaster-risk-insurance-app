// Listed trigger choices come only from supplied source levels: missing depths
// and historical zeros retain their meaning. Custom thresholds (below) are
// estimated from these levels and always labeled as estimates.
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
  if (peril === "satellite_fire") return {
    label: "Observed annual satellite fire-detection frequency",
    note: "Share of complete 2013–2025 years with at least one NASA FIRMS presumed vegetation-fire detection (Suomi NPP VIIRS) within the selected radius at or above this fire intensity (fire radiative power, MW). Satellite detections only, including crop and planned burns; fires can be missed under cloud or between passes. Not a calibrated future probability or the chance that this property burns.",
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
    note: "Modeled chance of meeting or exceeding this level in one year at the selected location. Listed levels use the source value directly; custom thresholds are labeled estimates.",
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

// Custom thresholds: an estimate derived from the supplied levels, never a
// source value. Between two levels the exceedance probability is interpolated
// linearly in log-probability (linearly when a bracket touches zero). Beyond
// the supplied range the nearest segment's log-probability slope is extended,
// which assumes an exponential tail. Lower and upper source frequencies are
// estimated separately so rounded-level ranges carry through.
function hazardLogLinear(x0, y0, x1, y1, x) {
  if (x1 === x0) return y0;
  const weight = (x - x0) / (x1 - x0);
  if (y0 > 0 && y1 > 0) return Math.exp(Math.log(y0) + weight * Math.log(y1 / y0));
  return y0 + weight * (y1 - y0);
}

function hazardTailEstimate(levels, key, severity) {
  const lowest = levels[0], highest = levels[levels.length - 1];
  if (severity < lowest.severity) {
    const next = levels[1];
    // A flat or rising segment gives no tail slope; exceedance cannot fall as
    // the threshold drops, so the lowest level's frequency is a lower bound.
    if (!(lowest[key] > next[key]) || next[key] <= 0) return { value: lowest[key], bound: "lower" };
    return { value: Math.min(100, hazardLogLinear(lowest.severity, lowest[key], next.severity, next[key], severity)) };
  }
  // Above the range: exceedance cannot rise with severity. A zero (or a flat
  // segment) at the top only bounds the estimate from above.
  const positive = levels.filter(level => level[key] > 0);
  if (highest[key] <= 0 || positive.length < 2) return { value: highest[key], bound: "upper" };
  const a = positive[positive.length - 2], b = positive[positive.length - 1];
  if (!(a[key] > b[key])) return { value: b[key], bound: "upper" };
  return { value: hazardLogLinear(a.severity, a[key], b.severity, b[key], severity) };
}

// options.extrapolate = false (observed-record curves such as nearby reported fires): never extend a
// slope past the data. Above the highest level the frequency there is an upper bound (exceedance cannot
// rise with severity); below the lowest it is a lower bound.
function hazardCustomTriggerLevel(levels, severity, options = {}) {
  if (!Array.isArray(levels) || !levels.length) return { error: "No source levels are loaded to estimate from." };
  if (!Number.isFinite(severity) || severity < 0) return { error: "Enter a non-negative threshold." };
  const exact = levels.find(level => level.severity === severity);
  if (exact) return { ...exact, custom: true, method: "source", basis: [exact.severity] };
  const lowest = levels[0], highest = levels[levels.length - 1];
  if (severity > lowest.severity && severity < highest.severity) {
    const upperIndex = levels.findIndex(level => level.severity > severity);
    const a = levels[upperIndex - 1], b = levels[upperIndex];
    const estimate = key => Math.min(100, Math.max(0, hazardLogLinear(a.severity, a[key], b.severity, b[key], severity)));
    const values = [estimate("minimum"), estimate("maximum")];
    return { severity, minimum: Math.min(...values), maximum: Math.max(...values), custom: true,
      method: "interpolated", basis: [a.severity, b.severity] };
  }
  if (options.extrapolate === false) {
    const below = severity < lowest.severity, edge = below ? lowest : highest;
    return { severity, minimum: edge.minimum, maximum: edge.maximum, custom: true, method: "bounded",
      direction: below ? "below" : "above", bound: below ? "lower" : "upper", basis: [edge.severity], reach: null };
  }
  if (levels.length < 2) return { error: "Only one source level is available, so there is no slope to extrapolate from." };
  const low = hazardTailEstimate(levels, "minimum", severity), high = hazardTailEstimate(levels, "maximum", severity);
  const below = severity < lowest.severity;
  const edge = below ? lowest : highest;
  const neighbour = below ? levels[1] : levels[levels.length - 2];
  const span = Math.abs(edge.severity - neighbour.severity);
  const distance = Math.abs(severity - edge.severity);
  const values = [low.value, high.value];
  return {
    severity, minimum: Math.max(0, Math.min(...values)), maximum: Math.min(100, Math.max(...values)), custom: true,
    method: "extrapolated", direction: below ? "below" : "above", bound: low.bound || high.bound || null,
    basis: [neighbour.severity, edge.severity].sort((x, y) => x - y),
    // How far past the data, measured in widths of the segment the slope came from.
    reach: span > 0 ? distance / span : Infinity,
  };
}

function hazardCustomTriggerNote(level, unit) {
  if (!level || level.error) return "";
  const [a, b] = level.basis.map(value => `${formatTriggerNumber(value)} ${unit}`);
  if (level.method === "source") return "This threshold matches a supplied source level, so the source value is used directly.";
  if (level.method === "bounded") return level.bound === "upper"
    ? `Above the highest observed level (${a}) the record has no years to count, and nothing is extrapolated. A larger threshold cannot be met more often, so the ${a} frequency is shown as an upper bound, not an estimate. Zero observed years does not mean zero future risk.`
    : `Below the lowest supplied level (${a}) nothing is extrapolated; a smaller threshold is met at least as often, so the ${a} frequency is shown as a lower bound, not an estimate.`;
  if (level.method === "interpolated") return `Estimated by log-probability interpolation between the supplied ${a} and ${b} levels. This is not a source value.`;
  if (level.bound === "upper" && level.maximum === 0) return `The highest supplied level (${b}) already has a 0% frequency, and exceedance cannot rise with severity, so 0% is shown. A zero in the source does not establish zero future risk.`;
  if (level.bound === "upper") return `Beyond the highest supplied level (${b}) the source gives no decreasing slope, so the ${b} frequency is shown as an upper bound, not an estimate.`;
  if (level.bound === "lower") return `Below the lowest supplied level (${a}) the source gives no slope, so the ${a} frequency is shown as a lower bound, not an estimate.`;
  const where = level.direction === "above" ? "above the highest" : "below the lowest";
  const caution = level.reach > 1
    ? ` This is ${level.reach.toFixed(1)}× the width of the fitted segment past the data, so treat it as highly uncertain.`
    : "";
  return `Extrapolated ${where} supplied level by extending the log-probability slope between ${a} and ${b} (an exponential-tail assumption). Real tails may be heavier or lighter.${caution}`;
}

function formatTriggerNumber(value) {
  return typeof formatHazardNumber === "function" ? formatHazardNumber(value) : String(Number(value.toPrecision(4)));
}

if (typeof module !== "undefined") module.exports = {
  hazardTriggerLevels, hazardTriggerBasis, hazardPoolContribution, hazardCustomTriggerLevel, hazardCustomTriggerNote,
};
