"""OpenAPI 3.1 contract consumed by the local API explorer."""
from api_v1 import VERSION, PERILS, SITE_CLASSES


def openapi_document():
    def parameter(name, schema, description, example=None, required=True, location="query"):
        result = {"name": name, "in": location, "required": required,
                  "description": description, "schema": schema}
        if example is not None:
            result["example"] = example
        return result

    peril = parameter("peril", {"type": "string", "enum": sorted(PERILS)},
                      "Hazard; auto uses county context and available local curves.", "flood", location="path")
    lat = parameter("lat", {"type": "number", "minimum": -90, "maximum": 90}, "Latitude in decimal degrees, WGS84. Required with lon when address is omitted.", 29.39741277, False)
    lon = parameter("lon", {"type": "number", "minimum": -180, "maximum": 180}, "Longitude in decimal degrees, WGS84. Required with lat when address is omitted.", -98.47084089, False)
    address = parameter("address", {"type": "string", "minLength": 1},
                        "Full US street address, city and state; optional ZIP. Use address OR lat and lon. Extra whitespace is collapsed; 100 characters maximum after cleanup. Check the matched address; typo corrections are not guaranteed.",
                        "100 Dolorosa St, San Antonio, TX", False)
    site = parameter("site_class", {"type": "string", "enum": sorted(SITE_CLASSES), "default": "BC"},
                     "Earthquake site-class assumption; accepted for every hazard.", "BC", False)
    payout = parameter("payout_usd", {"type": "number", "minimum": 0, "maximum": 1e12, "multipleOf": 0.01},
                       "Fixed payout in USD, paid at most once per year. Zero is valid.", 50000)
    bounds = [parameter(name, {"type": "number", "minimum": -limit, "maximum": limit},
                        "WGS84 area bound; south < north and west < east. Dateline-crossing areas are unsupported.", example)
              for name, limit, example in [("south", 90, 29.39), ("west", 180, -98.48),
                                            ("north", 90, 29.40), ("east", 180, -98.47)]]
    schemas = {
        "Meta": {"type": "object", "required": ["generated_at", "request", "purpose"], "properties": {
            "generated_at": {"type": "string", "format": "date-time", "description": "Response generation time, not source observation or model publication time."},
            "request": {"type": "object", "description": "Validated inputs including defaults and resolved coordinates for address requests."},
            "location": {"$ref": "#/components/schemas/AddressMatch"},
            "purpose": {"type": "string"}}},
        "Point": {"type": "object", "required": ["severity", "frequency_pct"], "properties": {
            "severity": {"type": ["number", "null"], "minimum": 0, "description": "Null is missing data, never a zero severity."},
            "frequency_pct": {"type": "number", "minimum": 0, "maximum": 100},
            "evaluated_samples": {"type": "integer", "minimum": 0},
            "missing_samples": {"type": "integer", "minimum": 0},
            "interpolated_samples": {"type": "integer", "minimum": 0}}},
        "Range": {"type": "object", "required": ["minimum", "maximum"], "properties": {
            "minimum": {"type": "number", "minimum": 0}, "maximum": {"type": "number", "minimum": 0}}},
        "Hazard": {"type": "object", "required": ["peril", "status", "points"], "properties": {
            "peril": {"type": "string", "enum": sorted(PERILS)}, "status": {"enum": ["available", "unavailable"]},
            "metric": {"type": "string"}, "unit": {"type": "string"}, "source_name": {"type": "string"},
            "source_url": {"type": "string"}, "frequency_label": {"type": "string"},
            "hazard_kind": {"enum": ["scenario_intensity", "potential_intensity_exceedance"]},
            "annual_probability_available": {"type": "boolean"},
            "actual_wildfire_probability_available": {"type": "boolean"},
            "scenario": {"type": "object", "description": "Potential-fire fixed live moisture, snow screening, wind limit and spin-up assumptions."},
            "landscape": {"type": "object", "description": "Potential-fire LANDFIRE input values, vintages, nominal resolution and source response hashes."},
            "trigger_unavailable_reason": {"type": "string"},
            "hazard_value": {"type": "object", "required": ["value", "unit", "statistic"], "properties": {
                "value": {"type": ["number", "null"], "minimum": 0},
                "unit": {"const": "kW/m"}, "statistic": {"type": "string"}}},
            "provenance": {"type": "object", "description": "Source retrieval timestamps, URLs and SHA256 hashes. Historical heat retains sources for station inventory, station metadata and daily record; area intensity results retain per-sample provenance."},
            "frequency_basis": {"type": "string"},
            "station": {"type": "object", "description": "For heat, the qualifying historical station proxy; distinct from the independent current METAR station search.",
                "required": ["id", "name", "latitude", "longitude", "distance_km", "url"], "properties": {
                    "id": {"type": "string"}, "name": {"type": "string"}, "latitude": {"type": "number"},
                    "longitude": {"type": "number"}, "distance_km": {"type": "number", "minimum": 0, "maximum": 100}, "url": {"type": "string", "format": "uri"}}},
            "baseline": {"type": "object", "required": ["start_year", "end_year", "complete_years", "minimum_complete_years", "excluded_years"], "properties": {
                "start_year": {"const": 1991}, "end_year": {"const": 2020}, "complete_years": {"type": "integer", "minimum": 0, "maximum": 30},
                "minimum_complete_years": {"const": 20}, "excluded_years": {"type": "array", "items": {"type": "integer", "minimum": 1991, "maximum": 2020}}}},
            "station_selection": {"type": "object", "description": "Historical heat: first five baseline-spanning US stations within 100 km are checked in distance order until one has at least 20 complete years. Includes checked station IDs, distances, completeness and source hashes."},
            "annual_maxima": {"type": "array", "minItems": 30, "maxItems": 30, "items": {"type": "object",
                "required": ["year", "expected_days", "valid_days", "complete", "maximum_c", "maximum_date"], "properties": {
                    "year": {"type": "integer", "minimum": 1991, "maximum": 2020}, "expected_days": {"enum": [365, 366]},
                    "valid_days": {"type": "integer", "minimum": 0, "maximum": 366}, "complete": {"type": "boolean"},
                    "maximum_c": {"type": ["number", "null"]}, "maximum_date": {"type": ["string", "null"], "format": "date"}}}},
            "source_references": {"type": "array", "items": {"type": "object"}},
            "points": {"type": "array", "items": {"$ref": "#/components/schemas/Point"}},
            "reason": {"type": "string"}, "details": {"type": "array"},
            "notes": {"type": "array", "items": {"type": "string"}},
            "selection": {"type": ["object", "null"], "description": "Automatic selection explanation when requested."}}},
        "Comparison": {"type": "object", "required": ["severity", "frequency_pct", "expected_annual_contribution_usd"], "properties": {
            "severity": {"type": "number", "minimum": 0},
            "frequency_pct": {"$ref": "#/components/schemas/Range"},
            "expected_annual_contribution_usd": {"$ref": "#/components/schemas/Range"}}},
        "TriggerComparisons": {"allOf": [{"$ref": "#/components/schemas/Hazard"}, {"type": "object",
            "required": ["payout_usd", "comparisons", "frequency_basis", "calculation", "assumptions"], "properties": {
                "payout_usd": {"type": "number", "minimum": 0},
                "comparisons": {"type": "array", "items": {"$ref": "#/components/schemas/Comparison"}},
                "frequency_basis": {"enum": ["historical_annual_frequency", "research_estimate", "modeled_annual_probability", "unavailable"]},
                "calculation": {"type": "string"}, "assumptions": {"type": "array", "items": {"type": "string"}}}}]},
        "Area": {"allOf": [{"$ref": "#/components/schemas/Hazard"}, {"type": "object", "required": ["area"], "properties": {
            "area": {"type": "object", "required": ["bounds", "centroid", "sample_count", "method", "aggregation", "samples"], "properties": {
                "bounds": {"type": "object"}, "centroid": {"type": "object"},
                "sample_count": {"const": 25}, "method": {"type": "string"},
                "aggregation": {"const": "arithmetic_mean"}, "samples": {"type": "array"}}}}}]},
        "HeatScreening": {"type": "object", "description": "Derived station-report snapshot for shade/light wind, not WBGT or an annual hazard probability. Units are encoded in field names. Nullable estimates remain unavailable, never zero-filled. See docs/heat-and-wind.md for the bounded display envelope and thresholds.",
            "required": ["status", "data_kind", "metric", "observed_at", "air_temperature_c", "dew_point_c", "relative_humidity_pct", "heat_index_c", "heat_index_f", "category", "method", "reason", "source_urls"],
            "properties": {"status": {"enum": ["available", "unavailable"]}, "data_kind": {"const": "observation_derived"},
                "metric": {"const": "Heat Index (shade)"}, "observed_at": {"type": ["string", "null"], "format": "date-time"},
                "air_temperature_c": {"type": ["number", "null"]}, "dew_point_c": {"type": ["number", "null"]},
                "relative_humidity_pct": {"type": ["number", "null"], "minimum": 0, "maximum": 100},
                "heat_index_c": {"type": ["number", "null"]}, "heat_index_f": {"type": ["number", "null"]},
                "category": {"type": ["string", "null"]}, "method": {"type": "string"}, "reason": {"type": "string"},
                "source_urls": {"type": "array", "items": {"type": "string", "format": "uri"}}}},
        "Stations": {"anyOf": [{"$ref": "#/components/schemas/LegacyStation"}, {"$ref": "#/components/schemas/NearbyStations"}]},
        "NearbyStation": {"type": "object", "required": ["id", "name", "network", "latitude", "longitude", "distance_km", "measured_variables", "url", "observation"], "properties": {
            "id": {"type": "string"}, "name": {"type": "string"}, "network": {"type": "string"},
            "latitude": {"type": "number", "minimum": -90, "maximum": 90}, "longitude": {"type": "number", "minimum": -180, "maximum": 180},
            "distance_km": {"type": "number", "minimum": 0, "maximum": 200},
            "measured_variables": {"type": "array", "items": {"type": "string"}},
            "variables_note": {"type": "string"}, "url": {"type": "string", "format": "uri"},
            "observation": {"type": "object", "required": ["status", "message"], "properties": {
                "status": {"enum": ["available", "stale", "missing", "unavailable"]},
                "message": {"type": "string"}, "observed_at": {"type": "string", "format": "date-time"},
                "age_minutes": {"type": "number", "minimum": 0},
                "heat": {"$ref": "#/components/schemas/HeatScreening"},
                "values": {"type": "array", "items": {"type": "object", "required": ["variable", "value", "unit"], "properties": {
                    "variable": {"type": "string"}, "value": {"type": ["number", "string"]}, "unit": {"type": "string"}}}}}}}},
        "NearbyStations": {"type": "object", "required": ["status", "stations", "network", "network_id", "requested_count", "returned_count", "fewer_than_requested", "search_radius_km", "coverage", "catalog_fetched_at", "message"], "properties": {
            "status": {"enum": ["available", "not_found"]}, "network_id": {"const": "awc_metar"}, "network": {"type": "string"},
            "requested_count": {"type": "integer", "minimum": 1, "maximum": 50}, "returned_count": {"type": "integer", "minimum": 0, "maximum": 50},
            "fewer_than_requested": {"type": "boolean"}, "search_radius_km": {"const": 200}, "coverage": {"type": "string"},
            "catalog_fetched_at": {"type": "string", "format": "date-time"}, "message": {"type": "string"},
            "stations": {"type": "array", "maxItems": 50, "items": {"$ref": "#/components/schemas/NearbyStation"}}}},
        "LegacyStation": {"type": "object", "required": ["peril", "status", "station", "network", "search_radius_km", "message"], "properties": {
            "peril": {"type": "string"}, "status": {"enum": ["available", "not_found"]},
            "station": {"type": ["object", "null"], "description": "Station metadata including ID, name, coordinates, distance_km and URL; no live readings."},
            "network": {"type": "string"}, "search_radius_km": {"type": "number"}, "message": {"type": "string"}}},
        "Health": {"type": "object", "required": ["status", "service", "provider_health_checked"], "properties": {
            "status": {"const": "available"}, "service": {"type": "string"}, "provider_health_checked": {"const": False}}},
        "AddressMatch": {"type": "object", "required": ["status", "normalized_address", "source_name", "source_url", "approximate", "note"], "properties": {
            "status": {"enum": ["matched", "no_match", "ambiguous"]}, "normalized_address": {"type": "string"},
            "matched_address": {"type": "string"}, "latitude": {"type": "number"}, "longitude": {"type": "number"},
            "source_name": {"type": "string"}, "source_url": {"type": "string"}, "approximate": {"const": True},
            "note": {"type": "string"}, "candidates": {"type": "array", "items": {"type": "object"}}}},
        "Geocode": {"type": "object", "required": ["status", "match_status", "normalized_address", "approximate", "note"], "properties": {
            "status": {"enum": ["available", "unavailable"]}, "match_status": {"enum": ["matched", "no_match", "ambiguous"]},
            "normalized_address": {"type": "string"}, "matched_address": {"type": "string"},
            "latitude": {"type": "number"}, "longitude": {"type": "number"}, "approximate": {"const": True},
            "note": {"type": "string"}, "candidates": {"type": "array", "items": {"type": "object"}}}},
        "Error": {"type": "object", "required": ["api_version", "status", "data", "meta", "error"], "properties": {
            "api_version": {"const": VERSION}, "status": {"const": "error"}, "data": {"type": "null"},
            "meta": {"$ref": "#/components/schemas/Meta"},
            "error": {"type": "object", "required": ["code", "message"], "properties": {
                "code": {"enum": ["invalid_request", "not_found", "method_not_allowed", "provider_unavailable", "address_not_found", "address_ambiguous"]},
                "message": {"type": "string"}, "details": {"$ref": "#/components/schemas/AddressMatch"}}}}},
    }

    # Historical potential-fire years have hourly model coverage, not daily heat observations.
    historical_year = schemas["Hazard"]["properties"]["annual_maxima"]["items"]
    schemas["Hazard"]["properties"]["annual_maxima"]["items"] = {"oneOf": [historical_year, {
        "type": "object", "required": ["year", "complete", "expected_hours"], "properties": {
            "year": {"type": "integer", "minimum": 1991, "maximum": 2020},
            "complete": {"type": "boolean"}, "expected_hours": {"enum": [8760, 8784]},
            "valid_hours": {"type": "integer", "minimum": 0, "maximum": 8784},
            "maximum_kw_m": {"type": "number", "minimum": 0}, "peak": {"type": "object"}}}]}
    schemas["Hazard"]["properties"]["annual_maxima"]["description"] = "Heat: daily station maxima. potential_fire_intensity: modeled hourly annual maxima on a fixed landscape, not observed historical fire intensities."

    def response_schema(data_schema):
        return {"type": "object", "required": ["api_version", "status", "data", "meta"], "properties": {
            "api_version": {"const": VERSION}, "status": {"enum": ["available", "unavailable", "not_found"]},
            "meta": {"$ref": "#/components/schemas/Meta"}, "data": {"$ref": f"#/components/schemas/{data_schema}"}}}

    def operation(name, summary, description, params, schema):
        responses = {"200": {"description": "Lookup completed. Inspect status: unavailable and not_found are valid outcomes, not zero risk.",
                             "content": {"application/json": {"schema": response_schema(schema)}}}}
        for code, meaning in [("400", "Invalid, missing, duplicate or unknown parameters."), ("404", "Unknown endpoint."),
                              ("405", "Unsupported method; use GET."), ("422", "Address could not be resolved uniquely; inspect error.details and correct the input."), ("502", "Provider failure; retry later.")]:
            responses[code] = {"description": meaning, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}}
        operation = {"operationId": name, "summary": summary, "description": description, "parameters": params, "responses": responses}
        if {"address", "lat", "lon"}.issubset(p["name"] for p in params):
            operation["x-location-inputs"] = ["coordinates", "address"]
            operation["description"] += " Supply either address or both lat and lon. Address requests include the match in meta.location; unresolved or ambiguous addresses return HTTP 422 before hazard computation."
        return {"get": operation}

    return {"openapi": "3.1.0", "info": {"title": "NDApp Research API", "version": VERSION,
            "description": "Local research API backed by the same providers as the map. No authentication on this loopback-only server. Hazard coverage varies. Frequencies are percentages, not fractions. No forecasts, binding quotes or payouts are issued. Unknown, blank and duplicate query parameters are rejected."},
            "servers": [{"url": "/"}], "paths": {
                "/api/v1/health": operation("health", "Service health", "Confirms the API is responding; does not contact or verify data providers.", [], "Health"),
                "/api/v1/geocode": operation("geocodeAddress", "Resolve a street address", "Check a full US street address before running a hazard calculation. Returns a matched approximate point or unavailable with match_status=no_match/ambiguous. Multiple matches are never selected automatically.", [{**address, "required": True}], "Geocode"),
                "/api/v1/hazards/{peril}": operation("hazardLookup", "Hazard lookup", "Source severity–frequency points, units, provenance and model details. Missing coverage remains explicit. Wind (API key: cyclone) covers tropical cyclones only and reports historical frequency; wildfire is a research estimate. fireline_intensity returns Texas scenario intensity without annual probabilities. potential_fire_intensity returns 1991–2020 historical-weather potential surface-intensity exceedance in kW/m, fixed LANDFIRE fuels, annual maxima and provenance. It is not actual wildfire arrival AEP. First request may take several minutes; sources are cached. Point mode only.", [peril, lat, lon, address, site], "Hazard"),
                "/api/v1/trigger-comparisons/{peril}": operation("triggerComparisons", "Trigger comparisons", "Compare supported point thresholds for one fixed payout. No interpolation or extrapolation. Duplicate rounded thresholds retain a range; alternative rows must not be added together. Contributions rounded to cents, assuming at most one payout per year, excluding fees. fireline_intensity returns unavailable comparisons and frequency_basis=unavailable because the source has no annual probabilities.", [peril, lat, lon, address, site, payout], "TriggerComparisons"),
                "/api/v1/areas/{peril}": operation("areaAnalysis", "Area analysis", "Samples a fixed 5×5 grid including boundaries and center. Averages available probabilities at each severity, with interpolation inside valid brackets. Coverage may differ by threshold. This is not the probability of an event anywhere in the area. For fireline_intensity, averages native kW/m values instead, with no probability interpolation; area.samples retains values/provenance and area.summary gives min/max/sum/missing counts. Requests may be slow because they sample 25 points.", [peril, *bounds, site], "Area"),
                "/api/v1/monitoring-stations": operation("monitoringStations", "Nearest monitoring stations", "Search unique METAR weather stations from the worldwide NOAA AWC catalog within 200 km. Omit peril; network defaults to awc_metar and n defaults to 5. Distances are great-circle km, sorted locally, not provider-ordered. Latest reports include units and UTC times; older than 2 hours is stale. observation.heat adds a derived shaded Heat Index snapshot in Celsius/Fahrenheit with source-based screening bands; missing, stale, future or out-of-envelope data withholds HI. It is not WBGT, a forecast, an annual probability or a construction stop-work rule. Missing/failed observations do not hide station links. Fewer results are explicit. For legacy single-station clients only, supply peril without n or network to retain hazard-specific metadata lookup. These inputs cannot be combined. Stations do not change hazard curves or contributions.", [parameter("network", {"type": "string", "enum": ["awc_metar"]}, "Multi-station network; omit peril.", "awc_metar", False), parameter("n", {"type": "integer", "minimum": 1, "maximum": 50, "default": 5}, "Positive integer result count; omit peril.", 5, False), parameter("peril", {"type": "string", "enum": sorted(PERILS - {"auto", "heat", "potential_fire_intensity"})}, "Legacy single-station lookup only; omit n and network.", required=False), lat, lon, address], "Stations"),
            }, "components": {"schemas": schemas}}
