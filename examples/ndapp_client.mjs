/** Small dependency-free NDApp client for Node.js 22+ and modern browsers. */
export class NdappApiError extends Error {
  constructor(message, status, payload, retryAfter = null) {
    super(message);
    this.name = "NdappApiError";
    this.status = status;
    this.code = payload?.error?.code ?? "http_error";
    this.payload = payload;
    this.retryAfter = retryAfter;
  }
}

export function createNdappClient(baseUrl, { timeoutMs = 120_000, fetchImpl = globalThis.fetch, apiKey } = {}) {
  const origin = new URL(baseUrl);
  if (!["http:", "https:"].includes(origin.protocol) || origin.search || origin.hash) {
    throw new TypeError("Use an HTTP(S) base URL without a query or fragment.");
  }
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new TypeError("timeoutMs must be positive.");
  const base = origin.href.replace(/\/+$/, "");

  async function get(resource, parameters = {}) {
    const url = new URL(`${base}/api/v1/${resource}`);
    for (const [key, value] of Object.entries(parameters)) {
      if (value !== undefined) url.searchParams.set(key, String(value));
    }
    const headers = { Accept: "application/json" };
    if (apiKey) headers["X-API-Key"] = apiKey;
    const response = await fetchImpl(url, { headers, signal: AbortSignal.timeout(timeoutMs) });
    let payload;
    try {
      payload = await response.json();
    } catch {
      throw new NdappApiError("The server did not return JSON.", response.status, null);
    }
    if (!response.ok) {
      throw new NdappApiError(payload?.error?.message ?? `HTTP ${response.status}`,
        response.status, payload, response.headers.get("Retry-After"));
    }
    // An unavailable/not_found result is valid JSON, not a zero or a failed HTTP request.
    return payload;
  }

  const perilPath = (resource, peril) => `${resource}/${encodeURIComponent(peril)}`;
  return {
    health: () => get("health"),
    hazard: (peril, location) => get(perilPath("hazards", peril), location),
    compareTriggers: (peril, parameters) => get(perilPath("trigger-comparisons", peril), parameters),
    area: (peril, bounds) => get(perilPath("areas", peril), bounds),
    stations: parameters => get("monitoring-stations", parameters),
    nearestFire: location => get("nearest-fire", location),
    fireProximity: location => get("fire-proximity", location),
    geocode: address => get("geocode", { address }),
  };
}
