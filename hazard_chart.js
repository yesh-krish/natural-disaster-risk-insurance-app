// Pure chart renderer shared by every disaster. Frequency increases left to right.
// Missing severity is omitted; genuine source/model zeros stay at zero.
function escapeChartText(value) {
  return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

function formatHazardNumber(value) {
  if (!Number.isFinite(value)) return "—";
  if (value === 0) return "0";
  if (Math.abs(value) < 0.001) return value.toExponential(2);
  return Number(value.toPrecision(5)).toLocaleString("en-US", { maximumFractionDigits: 6 });
}

function hazardChartSVG(rawPoints, severityLabel, frequencyLabel, fixedMaximum) {
  const points = rawPoints.filter(p => Number.isFinite(p.frequency_pct) && p.frequency_pct >= 0 &&
    Number.isFinite(p.severity) && p.severity >= 0).sort((a, b) => a.frequency_pct - b.frequency_pct);
  if (!points.length) return null;
  const width = 620, height = 300, left = 76, right = 28, top = 18, bottom = 58;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const logarithmic = points.every(p => p.frequency_pct > 0);
  const domain = points.map(p => logarithmic ? Math.log10(p.frequency_pct) : p.frequency_pct);
  let low = Math.min(...domain), high = Math.max(...domain);
  if (low === high) {
    if (logarithmic) { low -= 0.5; high += 0.5; }
    else { low = 0; high = Math.max(1, high * 1.2); }
  }
  const maximum = fixedMaximum || Math.max(0.1, Math.max(...points.map(p => p.severity)) * 1.12);
  const x = p => left + ((logarithmic ? Math.log10(p) : p) - low) / (high - low) * plotWidth;
  const y = v => top + (1 - v / maximum) * plotHeight;
  let content = "";
  for (let i = 0; i <= 4; i++) {
    const severity = maximum * i / 4;
    content += `<line x1="${left}" y1="${y(severity)}" x2="${width-right}" y2="${y(severity)}" stroke="#e2e8f0"/>` +
      `<text x="${left-10}" y="${y(severity)+4}" text-anchor="end" font-size="11">${formatHazardNumber(severity)}</text>`;
    const domainTick = low + (high-low)*i/4;
    const probability = logarithmic ? 10 ** domainTick : domainTick;
    content += `<text x="${left+plotWidth*i/4}" y="${height-bottom+23}" text-anchor="middle" font-size="11">${formatHazardNumber(probability)}%</text>`;
  }
  content += `<line x1="${left}" y1="${top}" x2="${left}" y2="${height-bottom}" stroke="#94a3b8"/>` +
    `<line x1="${left}" y1="${height-bottom}" x2="${width-right}" y2="${height-bottom}" stroke="#94a3b8"/>`;
  if (points.length > 1) content += `<polyline points="${points.map(p => `${x(p.frequency_pct)},${y(p.severity)}`).join(" ")}" fill="none" stroke="#2563eb" stroke-width="2"/>`;
  for (const point of points) {
    content += `<circle cx="${x(point.frequency_pct)}" cy="${y(point.severity)}" r="4.5" fill="#1d4ed8"><title>${escapeChartText(severityLabel)}: ${formatHazardNumber(point.severity)}; frequency: ${formatHazardNumber(point.frequency_pct)}%</title></circle>`;
  }
  const label = `${frequencyLabel}${logarithmic ? " · log scale" : " · linear scale"}`;
  return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeChartText(severityLabel)} versus ${escapeChartText(frequencyLabel)}">` +
    `<title>${escapeChartText(severityLabel)} versus ${escapeChartText(frequencyLabel)}</title>${content}` +
    `<text x="${left+plotWidth/2}" y="${height-9}" text-anchor="middle" font-size="12">${escapeChartText(label)}</text>` +
    `<text transform="translate(17 ${top+plotHeight/2}) rotate(-90)" text-anchor="middle" font-size="12">${escapeChartText(severityLabel)}</text></svg>`;
}

function drawHazardChart(container, points, severityLabel, frequencyLabel = "Annual exceedance probability (%)", fixedMaximum) {
  const svg = hazardChartSVG(points, severityLabel, frequencyLabel, fixedMaximum);
  if (svg) container.innerHTML = svg;
  else {
    const message = document.createElement("p");
    message.textContent = "No government severity–frequency points are available for this location.";
    container.replaceChildren(message);
  }
}

if (typeof module !== "undefined") module.exports = { hazardChartSVG, formatHazardNumber };
