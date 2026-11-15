# NDApp API: developer handoff

NDApp supplies JSON research data over HTTP GET. Clients do not need the map,
blockchain libraries, or access to the dataset files. The API already has a
versioned contract; the standalone server uses the same calculations as the map.

## Run a copy locally

From the project directory, with Python 3.12 or newer:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-api.txt
.\.venv\Scripts\python.exe api_server.py
```

On macOS/Linux, use `.venv/bin/python` in place of the Windows executable.
If the virtual environment already exists, skip its creation.

Open `http://127.0.0.1:8000/api/docs` to try requests. The machine-readable
OpenAPI contract is at `/api/v1/openapi.json`. Root `/` redirects to the docs;
the standalone server does not serve the map, source code, or datasets.
`server.py` remains available for the original map and API together.

**127.0.0.1 only works on the machine running the server.** A remote student
needs a deployed HTTPS base URL, a shared-network server, or their own local
copy. Starting this server does not create an internet address.

## Call the API

Use the server origin as the base URL, without `/api/v1` at the end.
These examples use the local origin; substitute the hosted origin after deployment.

```bash
curl "http://127.0.0.1:8000/api/v1/health"
curl "http://127.0.0.1:8000/api/v1/hazards/flood?lat=29.39741277&lon=-98.47084089"
curl "http://127.0.0.1:8000/api/v1/trigger-comparisons/flood?lat=29.39741277&lon=-98.47084089&payout_usd=50000"
```

JavaScript (Node.js 22+ or a modern browser), using the included client:

```javascript
import { createNdappClient } from './examples/ndapp_client.mjs';

const api = createNdappClient('http://127.0.0.1:8000');
const result = await api.hazard('flood', {
  lat: 29.39741277, lon: -98.47084089,
});
if (result.status === 'available') {
  console.log(result.data.points, result.data.unit);
} else {
  console.log(result.status, result.data?.reason);
}
```

The import above assumes a script in the repository root. Copy the client into
another project and adjust the import path there. Clients also support
`compareTriggers`, `area`, `stations`, `nearestFire`, `fireProximity`, `geocode`
and `health`. Pass query parameter objects using the names in `/api/docs`.
For a US address, replace `lat` and `lon` with `address`; do not supply both.

The client throws `NdappApiError` on non-successful HTTP responses, preserving
`status`, `code`, `payload` and the `retryAfter` header. Connection failures and
timeouts reject the request. It does not retry expensive calculations automatically.
Default timeout is 120 seconds; set `{ timeoutMs: 600_000 }` for initial potential
fire intensity calculations, which can take several minutes. A client timeout
does not cancel a calculation already running on the server.

Python clients can use `examples/api_client.py --base-url http://127.0.0.1:8000`
to retrieve trigger comparisons and optionally export JSON/CSV.

## Response rules

- Check both the HTTP status and JSON `status`. HTTP 200 can mean `unavailable`
  or `not_found`; those results must never become zero risk.
- Successful responses include `api_version`, `status`, `data`, and `meta`.
  Errors have `status: "error"`, `data: null`, and `error.code` / `error.message`.
- Frequencies are percentages: 1 means 1%, not 100%. Read `unit`, sources and
  frequency interpretation from the response. Not every hazard has annual probabilities.
- `meta.generated_at` is response time, not the observation time. Source times
  and provenance, when available, remain in `data`.
- 400 means invalid input; 422 means an unresolved/ambiguous address; 502 means
  provider failure. The standalone server returns 503 with `Retry-After` when
  its four lookup slots are busy. Health stays responsive while lookups run.

## Data and remote hosting

The repository excludes `data/`. A fresh clone without the local rasters will
explicitly return missing coverage for affected hazards. Install the same datasets
before comparing against the existing workspace:

```bash
python prepare_data.py --tile n29w99
python prepare_fire_data.py --region CONUS
```

These download about 2 GB of extracted raster data for the current San Antonio
flood tile and CONUS fire inputs. Additional flood tiles need separate preparation.
Other lookups contact external providers and need outbound internet access.
Potential fire results and source caches are stored under `data/` too.

The included Docker configuration runs the API with one application process:

```bash
docker build -f Dockerfile.api -t ndapp-api .
docker run --rm -p 8000:8000 --mount type=bind,source=/absolute/path/to/NDApp/data,target=/app/data ndapp-api
```

Replace the source with the existing absolute data directory. For hosted use,
mount persistent storage at `/app/data` and populate it separately. The image
does not bundle the large local datasets. `HOST` and `PORT` configure the listener;
defaults inside the image are `0.0.0.0` and `8000`. Keep a single application
process so background calculations and their in-memory coordination stay together.

Server-to-server clients need no CORS setting. For a browser client on another
origin, set `NDAPP_ALLOWED_ORIGINS` to its exact origin, for example
`http://localhost:3000`, or a comma-separated list. Restart after changing it.
Browser preflight for GET is supported; CORS does not authenticate clients.

API keys are supported (November 9, 2026): set `NDAPP_API_KEYS` and clients send
`X-API-Key`; the server refuses to listen on the network without keys. See
[sharing the API with another student](share-api.md). No public host is configured
by these files, and a broadly public service should also configure request quotas
at the hosting gateway. Hosting is separate
from the API implementation and does not become free merely by omitting the map.

See [the full API guide](api.md) and live OpenAPI contract for endpoints and
scientific coverage. This service returns research estimates and observations;
it does not execute transactions, verify insurance claims, or issue payouts.
