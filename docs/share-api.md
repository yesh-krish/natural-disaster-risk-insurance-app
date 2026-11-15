# Share the NDApp API with another student

The API runs on your computer (`api_server.py`). Another student calls it over the
network with an **API key** you give them. Health and docs stay public; every data
request needs the key in an `X-API-Key` header.

## You (the host)

1. Install once: `.venv\Scripts\python.exe -m pip install -r requirements-api.txt`
2. Make a key and keep it private (one key per person, so you can revoke one later):

   ```bash
   .venv\Scripts\python.exe api_server.py --new-key
   ```

3. Start the API with the key, listening on the network (Command Prompt):

   ```bash
   set NDAPP_API_KEYS=paste-the-key-here
   .venv\Scripts\python.exe api_server.py --host 0.0.0.0 --port 8000
   ```

   Several keys: `set NDAPP_API_KEYS=key1,key2`. Without a key the server refuses to
   listen on the network. Your laptop must stay on, awake and online while they use it.

4. Give them **a reachable address** (below) and **their key**. Send the key privately,
   not in a shared chat or the repository.

### Making it reachable from their machine

`http://127.0.0.1:8000` only works on your laptop. Pick one:

| Option | Cost | When to use | Address they use |
| --- | --- | --- | --- |
| **Tailscale** (recommended) | Free personal plan | Different networks; stays private | `http://<your Tailscale IP>:8000` |
| **Cloudflare quick tunnel** | Free, no account | Quick test or demo | Temporary `https://….trycloudflare.com` |
| **Same Wi-Fi** | Free | Both on the same home network | `http://<your Wi-Fi IP>:8000` |
| **Hosting** (Render, Jetstream2) | Free tier / paid / sponsored | Permanent, laptop not needed | Hosting URL |

- **Tailscale:** both install Tailscale and sign in. Then share your laptop with them
  ("Share" on your device in the Tailscale admin page). Use the `100.x.x.x` address
  Tailscale shows for your laptop. Nothing is exposed to the public internet.
- **Cloudflare quick tunnel:** install `cloudflared`, run
  `cloudflared tunnel --url http://localhost:8000`, and send the printed `https` URL.
  It changes every time you restart it, and anyone with the URL can reach the
  server, so the API key is what protects it.
- **Same Wi-Fi:** this often fails. Windows blocks incoming connections on networks
  marked **Public** (this laptop's Wi-Fi was Public on 2026-11-09), and campus Wi-Fi
  usually blocks device-to-device traffic. If you try it, allow Python through Windows
  Firewall for private networks only, and set your home network to Private.

## Them (the other student)

They do not need this repository, the datasets or Python, only the address and key.

Check the connection (no key needed):

```bash
curl "BASE_URL/api/v1/health"
```

Request data with the key:

```bash
curl -H "X-API-Key: THEIR_KEY" "BASE_URL/api/v1/hazards/flood?lat=29.4242&lon=-98.4933"
curl -H "X-API-Key: THEIR_KEY" "BASE_URL/api/v1/fire-proximity?lat=29.4242&lon=-98.4933"
```

Python (no packages needed):

```python
import json, urllib.request
req = urllib.request.Request("BASE_URL/api/v1/risk-index?lat=29.4242&lon=-98.4933",
                             headers={"X-API-Key": "THEIR_KEY"})
print(json.load(urllib.request.urlopen(req, timeout=90))["data"]["tracts"][0]["composite"])
```

JavaScript, with `examples/ndapp_client.mjs` copied into their project:

```javascript
import { createNdappClient } from './ndapp_client.mjs';
const api = createNdappClient('BASE_URL', { apiKey: 'THEIR_KEY' });
const result = await api.fireProximity({ lat: 29.4242, lon: -98.4933 });
```

Interactive docs and every endpoint: `BASE_URL/api/docs`. Click **Authorize** and
enter the key to try requests in the browser.

## What they'll see

- `401 unauthorized`: missing or wrong key.
- `503 service_busy`: four lookups are already running; wait and retry (`Retry-After`).
- HTTP 200 with `status: unavailable` or `not_found`: a valid answer meaning no data
  here. It is not zero risk.
- The flood curve needs the Texas flood tiles in your `data/` folder; the other routes
  call government services live, so they can be slow or occasionally fail (`502`).

## Limits

- The API key is a shared secret, not a full login. Over a plain `http://` address on
  the same Wi-Fi, others on that network could see it in transit; Tailscale and
  Cloudflare tunnels encrypt the connection.
- To revoke someone, remove their key from `NDAPP_API_KEYS` and restart the server.
- Every request calls government data services, so don't share keys widely.
