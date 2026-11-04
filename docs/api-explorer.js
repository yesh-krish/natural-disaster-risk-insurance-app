const endpoint = document.getElementById("endpoint");
const fields = document.getElementById("parameter-fields");
const send = document.getElementById("send-request");
const state = document.getElementById("request-state");
const responseBody = document.getElementById("response-body");
const responseStatus = document.getElementById("response-status");
const requestURL = document.getElementById("request-url");
let specification;
let pending;

function clearResponse() {
  pending?.abort();
  pending = null;
  send.disabled = false;
  requestURL.hidden = true;
  document.getElementById("curl-details").hidden = true;
  document.getElementById("address-result").hidden = true;
  responseStatus.textContent = "";
  responseBody.textContent = "Send a request to see its JSON response.";
  state.textContent = "Ready. Requests run against this local server.";
}

function updateLocationMode() {
  const addressMode = document.getElementById("location-input").value === "address";
  for (const name of ["address", "lat", "lon"]) {
    const input = document.getElementById(`parameter-${name}`);
    input.disabled = (name === "address") !== addressMode;
    input.required = !input.disabled;
    input.parentElement.hidden = input.disabled;
    input.parentElement.firstChild.textContent = `${name} *`;
  }
  clearResponse();
}

function renderParameters() {
  pending?.abort();
  pending = null;
  send.disabled = false;
  fields.replaceChildren();
  const operation = specification.paths[endpoint.value].get;
  document.getElementById("operation-description").textContent = operation.description;
  if (operation["x-location-inputs"]) {
    const label = document.createElement("label");
    label.textContent = "Location input";
    const mode = document.createElement("select");
    mode.id = "location-input";
    mode.append(new Option("Coordinates", "coordinates"), new Option("Street address", "address"));
    mode.addEventListener("change", updateLocationMode);
    label.append(mode);
    fields.append(label);
  }
  for (const parameter of operation.parameters) {
    const label = document.createElement("label");
    label.textContent = parameter.name + (parameter.required ? " *" : " (optional)");
    const schema = parameter.schema;
    const input = document.createElement(schema.enum ? "select" : "input");
    input.name = parameter.name;
    input.required = parameter.required;
    input.id = `parameter-${parameter.name}`;
    if (schema.enum) {
      if (!parameter.required) input.append(new Option("(omit)", ""));
      schema.enum.forEach(value => input.append(new Option(value === "cyclone" ? "Wind (tropical cyclones; API: cyclone)" : value, value)));
    } else {
      input.type = ["number", "integer"].includes(schema.type) ? "number" : "text";
      if (input.type === "number") {
        input.step = schema.type === "integer" ? 1 : schema.multipleOf || "any";
        if (schema.minimum !== undefined) input.min = schema.minimum;
        if (schema.maximum !== undefined) input.max = schema.maximum;
      }
    }
    input.value = parameter.example ?? schema.default ?? "";
    const help = document.createElement("small");
    help.id = `help-${parameter.name}`;
    help.textContent = parameter.description;
    input.setAttribute("aria-describedby", help.id);
    label.append(input, help);
    fields.append(label);
  }
  if (operation["x-location-inputs"]) updateLocationMode();
  else clearResponse();
}

document.getElementById("api-form").addEventListener("submit", async event => {
  event.preventDefault();
  const operation = specification.paths[endpoint.value].get;
  let path = endpoint.value;
  const query = new URLSearchParams();
  for (const parameter of operation.parameters) {
    const input = document.getElementById(`parameter-${parameter.name}`);
    if (input.disabled) continue;
    const value = input.value.trim();
    if (parameter.in === "path") path = path.replace(`{${parameter.name}}`, encodeURIComponent(value));
    else if (value !== "") query.set(parameter.name, value);
  }
  const url = new URL(path, location.origin);
  url.search = query.toString();
  requestURL.textContent = url.href;
  requestURL.href = url.href;
  requestURL.hidden = false;
  document.getElementById("curl-details").hidden = false;
  document.getElementById("curl-command").textContent = `curl "${url.href}"`;
  pending?.abort();
  const controller = new AbortController();
  pending = controller;
  const timeout = setTimeout(() => controller.abort(), 90000);
  send.disabled = true;
  state.textContent = "Request in progress… Area and external-provider lookups may take longer.";
  responseStatus.textContent = "Loading";
  document.getElementById("address-result").hidden = true;
  try {
    const response = await fetch(url, { signal: controller.signal });
    const body = await response.json();
    if (pending !== controller) return;
    responseBody.textContent = JSON.stringify(body, null, 2);
    responseStatus.textContent = `HTTP ${response.status} · ${body.status || "response"}`;
    const location = body.meta?.location || (body.data?.match_status ? body.data : null);
    if (location?.matched_address) {
      const matched = document.getElementById("address-result");
      matched.textContent = `Address used: ${location.normalized_address}. Matched: ${location.matched_address}. Coordinates: ${location.latitude}, ${location.longitude}. ${location.note}`;
      matched.hidden = false;
    }
    state.textContent = response.ok ? "Lookup completed. Check the response status and coverage before interpreting results." : body.error?.message || "Request failed.";
  } catch (error) {
    if (pending !== controller) return;
    responseStatus.textContent = "Request failed";
    state.textContent = controller.signal.aborted ? "Request timed out. Try again or use a smaller lookup." : "Could not read the API response. Restart the server with the latest code and retry.";
    responseBody.textContent = state.textContent;
  } finally {
    clearTimeout(timeout);
    if (pending === controller) { pending = null; send.disabled = false; }
  }
});

endpoint.addEventListener("change", renderParameters);
(async () => {
  try {
    const response = await fetch("/api/v1/openapi.json");
    if (!response.ok) throw new Error("Specification unavailable");
    specification = await response.json();
    Object.entries(specification.paths).forEach(([path, methods]) => endpoint.append(new Option(`GET · ${methods.get.summary}`, path)));
    endpoint.value = "/api/v1/trigger-comparisons/{peril}";
    endpoint.disabled = false;
    renderParameters();
  } catch (error) {
    state.textContent = "API specification unavailable. Restart the Python server with the latest code and reload this page.";
  }
})();
