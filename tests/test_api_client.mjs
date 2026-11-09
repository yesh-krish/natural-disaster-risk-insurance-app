import test from "node:test";
import assert from "node:assert/strict";
import { createNdappClient, NdappApiError } from "../examples/ndapp_client.mjs";

test("client encodes addresses and preserves payout zero", async () => {
  let requested;
  const client = createNdappClient("http://localhost:8000/", {
    fetchImpl: async (url, options) => {
      requested = url;
      assert.ok(options.signal instanceof AbortSignal);
      return Response.json({ status: "available", data: {} });
    },
  });
  await client.compareTriggers("flood", { address: "100 Dolorosa St, San Antonio, TX", payout_usd: 0 });
  assert.equal(requested.pathname, "/api/v1/trigger-comparisons/flood");
  assert.equal(requested.searchParams.get("address"), "100 Dolorosa St, San Antonio, TX");
  assert.equal(requested.searchParams.get("payout_usd"), "0");
});

test("missing coverage stays unavailable, with null values intact", async () => {
  const payload = { status: "unavailable", data: { points: [], depth: null } };
  const client = createNdappClient("http://localhost:8000", { fetchImpl: async () => Response.json(payload) });
  assert.deepEqual(await client.hazard("flood", { lat: 0, lon: 0 }), payload);
});

test("HTTP errors preserve status, error code and retry hint without retrying", async () => {
  let calls = 0;
  const client = createNdappClient("http://localhost:8000", { fetchImpl: async () => {
    calls++;
    return Response.json({ status: "error", error: { code: "service_busy", message: "Busy" } },
      { status: 503, headers: { "Retry-After": "2" } });
  } });
  await assert.rejects(client.health(), error => error instanceof NdappApiError &&
    error.status === 503 && error.code === "service_busy" && error.retryAfter === "2");
  assert.equal(calls, 1);
});

test("proxy HTML errors produce an explicit client error", async () => {
  const client = createNdappClient("http://localhost:8000", { fetchImpl: async () =>
    new Response("<html>Proxy unavailable</html>", { status: 502 }) });
  await assert.rejects(client.health(), error => error instanceof NdappApiError && error.status === 502);
});
