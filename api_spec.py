"""OpenAPI 3.1 contract consumed by the local API explorer."""
from api_v1 import VERSION, PERILS, SITE_CLASSES, STATION_PERILS, FIRE_RADII_KM, DEFAULT_FIRE_RADIUS_KM
from risk_context_schemas import context_schemas


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
    radius = parameter("radius_km", {"type": "number", "exclusiveMinimum": 0, "maximum": 20, "default": DEFAULT_FIRE_RADIUS_KM},
                       "Wildfire only: trigger distance in km for reported fires, any value above 0 up to 20 (the archive search distance). Computed directly from the reported fires within that distance; nothing is interpolated between distances. Rejected for other hazards.", 10, False)
    payout = parameter("payout_usd", {"type": "number", "minimum": 0, "maximum": 1e12, "multipleOf": 0.01},
                       "Fixed payout in USD, paid at most once per year. Zero is valid.", 50000)
    threshold = parameter("threshold", {"type": "number", "minimum": 0},
                          "Optional custom trigger threshold in the hazard's units (m, %g, kn, mm, °C or acres; for auto, the selected hazard's unit). Returned as custom_trigger: the source value when it matches a level, otherwise an estimate (log-probability interpolation between levels; exponential-tail extrapolation beyond them). Wildfire is never extrapolated: past its observed sizes the edge frequency is returned as a bound (method bounded).", 0.1, False)
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
            "annual_probability_available": {"type": "boolean"},
            "trigger_unavailable_reason": {"type": "string"},
            "radius_km": {"type": "number", "exclusiveMinimum": 0, "maximum": 20, "description": "Wildfire: trigger distance for reported fires (km)."},
            "record_period": {"type": "object", "description": "Wildfire: FPA FOD record years used (start_year, end_year, years)."},
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
                "calculation": {"type": "string"}, "assumptions": {"type": "array", "items": {"type": "string"}},
                "custom_trigger": {"$ref": "#/components/schemas/CustomTrigger"}}}]},
        "CustomTrigger": {"type": "object", "required": ["status", "threshold"],
            "description": "Present only when threshold is supplied. Same method as the map's custom threshold. Not a source value unless method is source.",
            "properties": {
                "status": {"enum": ["available", "unavailable"]}, "threshold": {"type": "number", "minimum": 0},
                "reason": {"type": "string", "description": "Why no estimate was made (unavailable only)."},
                "estimate": {"type": "boolean", "description": "False only when threshold equals a supplied source level."},
                "method": {"enum": ["source", "interpolated", "extrapolated", "bounded"], "description": "bounded: wildfire threshold outside the observed sizes; the edge frequency is an upper (above) or lower (below) bound."},
                "direction": {"enum": ["above", "below", None], "description": "Side of the supplied range for extrapolated or bounded thresholds; null otherwise."},
                "bound": {"enum": ["upper", "lower", None], "description": "Set when the edge frequency is returned as a bound, not an estimate: the source gives no slope, or (wildfire) the threshold is outside the observed sizes."},
                "basis_severities": {"type": "array", "items": {"type": "number"}, "description": "Source levels the value was derived from."},
                "reach_segment_widths": {"type": ["number", "null"], "description": "Extrapolation distance past the data in widths of the fitted segment; above 1 is highly uncertain."},
                "frequency_pct": {"$ref": "#/components/schemas/Range"},
                "expected_annual_contribution_usd": {"$ref": "#/components/schemas/Range"},
                "note": {"type": "string"}}},
        "Area": {"allOf": [{"$ref": "#/components/schemas/Hazard"}, {"type": "object", "required": ["area"], "properties": {
            "area": {"type": "object", "required": ["bounds", "centroid", "sample_count", "method", "aggregation", "samples"], "properties": {
                "bounds": {"type": "object"}, "centroid": {"type": "object"},
                "sample_count": {"enum": [1, 25], "description": "25 grid samples; 1 for wildfire, which uses the area's center."},
                "method": {"type": "string"},
                "aggregation": {"enum": ["arithmetic_mean", "center_point"]}, "samples": {"type": "array"}}}}}]},
        "HeatScreening": {"type": "object", "description": "Derived station-report snapshot for shade/light wind, not WBGT or an annual hazard probability. Units are encoded in field names. Nullable estimates remain unavailable, never zero-filled. See docs/heat-and-wind.md for the bounded display envelope and thresholds.",
            "required": ["status", "data_kind", "metric", "observed_at", "air_temperature_c", "dew_point_c", "relative_humidity_pct", "heat_index_c", "heat_index_f", "category", "method", "reason", "source_urls"],
            "properties": {"status": {"enum": ["available", "unavailable"]}, "data_kind": {"const": "observation_derived"},
                "metric": {"const": "Heat Index (shade)"}, "observed_at": {"type": ["string", "null"], "format": "date-time"},
                "air_temperature_c": {"type": ["number", "null"]}, "dew_point_c": {"type": ["number", "null"]},
                "relative_humidity_pct": {"type": ["number", "null"], "minimum": 0, "maximum": 100},
                "heat_index_c": {"type": ["number", "null"]}, "heat_index_f": {"type": ["number", "null"]},
                "category": {"type": ["string", "null"]}, "method": {"type": "string"}, "reason": {"type": "string"},
                "source_urls": {"type": "array", "items": {"type": "string", "format": "uri"}}}},
        "Stations": {"anyOf": [{"$ref": "#/components/schemas/LegacyStation"}, {"$ref": "#/components/schemas/NearbyStations"},
                               {"$ref": "#/components/schemas/HazardStations"}]},
        "HazardStations": {"type": "object", "required": ["peril", "status", "station", "stations", "network", "requested_count",
                                                          "returned_count", "fewer_than_requested", "search_radius_km", "message"], "properties": {
            "peril": {"type": "string"}, "status": {"enum": ["available", "not_found"]}, "network": {"type": "string"},
            "station": {"type": ["object", "null"], "description": "Nearest station (same as stations[0])."},
            "stations": {"type": "array", "maxItems": 50, "items": {"type": "object", "required": ["id", "name", "latitude", "longitude", "distance_km"],
                "properties": {"id": {"type": "string"}, "name": {"type": "string"}, "latitude": {"type": "number"},
                               "longitude": {"type": "number"}, "distance_km": {"type": "number", "minimum": 0}, "url": {"type": "string"}}}},
            "requested_count": {"type": "integer", "minimum": 1, "maximum": 50}, "returned_count": {"type": "integer", "minimum": 0, "maximum": 50},
            "fewer_than_requested": {"type": "boolean"}, "search_radius_km": {"const": 200}, "message": {"type": "string"}}},
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
        "FireProximity": {"type": "object", "required": ["status", "frequency_basis", "baseline", "radii_km", "frequencies", "annual_records", "size_exceedance", "source_url", "checked_at"], "properties": {
            "status": {"enum": ["available", "unavailable"]}, "frequency_basis": {"const": "historical_annual_reported_fire_frequency"},
            "radii_km": {"const": [5, 10, 20]}, "baseline": {"type": ["object", "null"], "properties": {
                "start_year": {"type": "integer"}, "end_year": {"type": "integer"}, "year_count": {"type": "integer", "minimum": 20},
                "local_reporting_completeness_verified": {"const": False}, "coverage_assumption": {"type": "string"}}},
            "source_url": {"type": "string", "format": "uri"}, "source_name": {"type": "string"},
            "checked_at": {"type": "string", "format": "date-time"}, "refresh_seconds": {"const": 300},
            "frequencies": {"type": "array", "maxItems": 3, "items": {"type": "object", "required": ["radius_km", "years_with_fire", "total_years", "frequency_pct", "qualifying_years", "reference_interval"], "properties": {
                "radius_km": {"enum": [5, 10, 20]}, "years_with_fire": {"type": "integer", "minimum": 0}, "total_years": {"type": "integer", "minimum": 20},
                "frequency_pct": {"type": "number", "minimum": 0, "maximum": 100}, "qualifying_years": {"type": "array", "items": {"type": "integer"}},
                "reference_interval": {"type": "object", "required": ["minimum_pct", "maximum_pct", "method"], "properties": {
                    "minimum_pct": {"type": "number", "minimum": 0, "maximum": 100}, "maximum_pct": {"type": "number", "minimum": 0, "maximum": 100}, "method": {"const": "wilson_95_binomial_reference"}}}}}},
            "annual_records": {"type": "array", "items": {"type": "object", "required": ["year", "nearest_distance_within_20_km", "within_radii", "nearest_fire"], "properties": {
                "year": {"type": "integer"}, "nearest_distance_within_20_km": {"type": ["number", "null"], "minimum": 0, "maximum": 20.00000001},
                "nearest_fire": {"type": ["object", "null"]}, "within_radii": {"type": "object", "required": ["5", "10", "20"], "additionalProperties": {"type": "boolean"}}}}},
            "size_exceedance": {"type": ["object", "null"], "description": "Reported fire size combined with distance. Null when unavailable.",
                "required": ["basis", "size_thresholds_acres", "records_in_query", "records_missing_size", "threshold_frequencies", "area_exceedance", "footprint_reach", "burned_area", "notes"], "properties": {
                "basis": {"const": "reported_size_and_distance"}, "size_thresholds_acres": {"const": [10, 100, 1000, 5000]},
                "records_in_query": {"type": "integer", "minimum": 0}, "records_missing_size": {"type": "integer", "minimum": 0},
                "threshold_frequencies": {"type": "array", "maxItems": 12, "description": "Share of baseline years with at least one reported fire of at least min_acres within radius_km.", "items": {"type": "object", "required": ["min_acres", "radius_km", "years_with_fire", "total_years", "frequency_pct", "qualifying_years", "reference_interval"], "properties": {
                    "min_acres": {"enum": [10, 100, 1000, 5000]}, "radius_km": {"enum": [5, 10, 20]}, "years_with_fire": {"type": "integer", "minimum": 0},
                    "total_years": {"type": "integer", "minimum": 20}, "frequency_pct": {"type": "number", "minimum": 0, "maximum": 100},
                    "qualifying_years": {"type": "array", "items": {"type": "integer"}}, "reference_interval": {"type": "object"}}}},
                "area_exceedance": {"type": "object", "description": "Burned-area exceedance curve per radius: for each size threshold, reported fires per year at least that size (Poisson rate), annual probability 1 - exp(-rate), observed share of years, and return period. Not extrapolated beyond the largest reported fire.",
                    "required": ["method", "thresholds_acres", "curves"], "properties": {
                    "method": {"const": "poisson_rate_of_reported_fire_size"}, "thresholds_acres": {"type": "array", "items": {"type": "number"}},
                    "curves": {"type": "array", "maxItems": 3, "items": {"type": "object", "required": ["radius_km", "fire_count", "total_years", "largest_reported_acres", "expected_annual_reported_acres", "points"], "properties": {
                        "radius_km": {"enum": [5, 10, 20]}, "fire_count": {"type": "integer", "minimum": 0}, "total_years": {"type": "integer", "minimum": 20},
                        "largest_reported_acres": {"type": ["number", "null"], "minimum": 0}, "expected_annual_reported_acres": {"type": "number", "minimum": 0},
                        "points": {"type": "array", "items": {"type": "object", "required": ["min_acres", "fires_at_or_above", "expected_annual_exceedances", "annual_probability_pct", "empirical_years_pct", "return_period_years"], "properties": {
                            "min_acres": {"type": "number", "exclusiveMinimum": 0}, "fires_at_or_above": {"type": "integer", "minimum": 0},
                            "expected_annual_exceedances": {"type": "number", "minimum": 0}, "annual_probability_pct": {"type": "number", "minimum": 0, "maximum": 100},
                            "empirical_years_pct": {"type": "number", "minimum": 0, "maximum": 100}, "return_period_years": {"type": ["number", "null"], "minimum": 1}}}}}}}}},
                "sized_fires": {"type": "object", "description": "Every baseline-year reported fire with a positive size within max_distance_km, as [year, distance_km, reported_acres] rows, nearest first. Lets clients compute the size and distance trigger for any distance up to max_distance_km.",
                    "required": ["fields", "max_distance_km", "rows"], "properties": {
                    "fields": {"const": ["year", "distance_km", "reported_acres"]}, "max_distance_km": {"const": 20},
                    "rows": {"type": "array", "items": {"type": "array", "prefixItems": [{"type": "integer"}, {"type": "number", "minimum": 0, "maximum": 20.0001}, {"type": "number", "exclusiveMinimum": 0}], "minItems": 3, "maxItems": 3}}}},
                "footprint_reach": {"type": "object", "description": "Years in which a reported fire's equivalent-circle footprint, centered on its reported point, covered the selection. Approximate geometry.",
                    "required": ["years_with_reach", "total_years", "frequency_pct", "qualifying_years", "reference_interval", "fires", "closest_footprint_gap_km", "omitted_fire_min_acres"], "properties": {
                    "years_with_reach": {"type": "integer", "minimum": 0}, "total_years": {"type": "integer", "minimum": 20},
                    "frequency_pct": {"type": "number", "minimum": 0, "maximum": 100}, "qualifying_years": {"type": "array", "items": {"type": "integer"}},
                    "reference_interval": {"type": "object"}, "fires": {"type": "array", "items": {"type": "object"}},
                    "closest_footprint_gap_km": {"type": ["number", "null"], "minimum": 0}, "closest_footprint_fire_id": {"type": ["string", "null"]},
                    "omits_fires_reported_beyond_km": {"const": 20}, "omitted_fire_min_acres": {"type": "number", "minimum": 0}}},
                "burned_area": {"type": "array", "maxItems": 2, "description": "Mean annual share of the circle covered by reported equivalent-circle footprints. A whole-circle average, not a property burn probability.", "items": {"type": "object", "required": ["radius_km", "circle_area_km2", "overlapping_reported_area_km2", "overlapping_reported_acres", "total_years", "mean_annual_burned_share_pct", "equivalent_return_interval_years", "omitted_fire_min_acres"], "properties": {
                    "radius_km": {"enum": [5, 10]}, "circle_area_km2": {"type": "number", "minimum": 0}, "overlapping_reported_area_km2": {"type": "number", "minimum": 0},
                    "overlapping_reported_acres": {"type": "number", "minimum": 0}, "total_years": {"type": "integer", "minimum": 20},
                    "mean_annual_burned_share_pct": {"type": "number", "minimum": 0}, "equivalent_return_interval_years": {"type": ["number", "null"], "minimum": 0},
                    "omitted_fire_min_acres": {"type": "number", "minimum": 0}}}},
                "notes": {"type": "array", "items": {"type": "string"}}}},
            "notes": {"type": "array", "items": {"type": "string"}}, "reason": {"type": "string"}}},
        "NearestFire": {"type": "object", "required": ["status", "time_window", "fire", "checked_at", "sources", "refresh_seconds"], "properties": {
            "status": {"enum": ["available", "not_found"]}, "time_window": {"const": "all_available_history"},
            "checked_at": {"type": "string", "format": "date-time"}, "refresh_seconds": {"const": 300},
            "sources": {"type": "array", "minItems": 2, "items": {"type": "object"}},
            "fire": {"type": ["object", "null"], "description": "Nearest dated reported wildfire point across both full-history sources; null only when both searches complete without a result.", "required": ["id", "name", "latitude", "longitude", "distance_km", "discovery_date", "source_name", "record_url"], "properties": {
                "id": {"type": "string"}, "name": {"type": "string"}, "latitude": {"type": "number", "minimum": -90, "maximum": 90},
                "longitude": {"type": "number", "minimum": -180, "maximum": 180}, "distance_km": {"type": "number", "minimum": 0},
                "discovery_date": {"type": "string", "format": "date"}, "discovered_at": {"type": "string", "format": "date-time"},
                "reported_acres": {"type": ["number", "null"], "minimum": 0}, "source_name": {"type": "string"}, "record_url": {"type": "string", "format": "uri"}}}}},
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
                "code": {"enum": ["invalid_request", "not_found", "method_not_allowed", "provider_unavailable", "address_not_found", "address_ambiguous", "unauthorized", "service_busy", "internal_error"]},
                "message": {"type": "string"}, "details": {"$ref": "#/components/schemas/AddressMatch"}}}}},
    }

    schemas.update(context_schemas())
    hazard_names = {"enum": ["flood", "earthquake", "wildfire"]}
    schemas["Assessment"] = {"type": "object", "required": [
        "status", "assessment_status", "assessment_id", "schema_version", "created_at", "assessment_type",
        "scope", "location", "hazard_status", "facts", "gaps", "evidence", "reason", "notes"], "properties": {
        "status": {"enum": ["available", "unavailable"]},
        "assessment_status": {"enum": ["complete", "partial", "unavailable", "awaiting_input", "failed"]},
        "assessment_id": {"type": "string"}, "schema_version": {"type": "string"},
        "created_at": {"type": "string", "format": "date-time"}, "assessment_type": {"const": "hazard_screening"},
        "scope": {"type": "object", "properties": {"hazards": {"type": "array", "items": hazard_names}}},
        "location": {"type": "object", "properties": {"lat": {"type": "number"}, "lon": {"type": "number"},
                                                      "confirmed": {"type": "boolean"}, "address": {"type": "string"},
                                                      "candidates": {"type": "array"}}},
        "hazard_status": {"type": "object", "additionalProperties": {
            "enum": ["evidence_available", "evidence_available_no_curve", "no_usable_evidence"]}},
        "facts": {"type": "array", "items": {"type": "object", "required": [
            "id", "evidence_id", "pointer", "label", "value", "unit", "hazard", "evidence_kind"], "properties": {
            "id": {"type": "string"}, "evidence_id": {"type": "string"}, "pointer": {"type": "string"},
            "label": {"type": "string"}, "unit": {"type": ["string", "null"]},
            "hazard": {"enum": ["flood", "earthquake", "wildfire", "context"]},
            "evidence_kind": {"enum": ["location", "modeled_hazard_curve", "current_observation", "regulatory_map",
                                       "terrain", "historical_catalog", "community_index", "historical_frequency"]}}}},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "object", "required": ["id", "tool", "status", "retrieved_at", "sha256"],
                     "properties": {"id": {"type": "string"}, "tool": {"type": "string"},
                                    "status": {"enum": ["available", "not_found", "unavailable", "error"]},
                                    "retrieved_at": {"type": "string", "format": "date-time"},
                                    "sha256": {"type": ["string", "null"]}, "source_name": {"type": ["string", "null"]},
                                    "source_url": {"type": ["string", "null"]}}}},
        "reason": {"type": ["string", "null"]}, "notes": {"type": "array", "items": {"type": "string"}}}}

    schemas["Hazard"]["properties"]["annual_maxima"]["description"] = "Heat: daily station maxima."

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
            "description": "Research API backed by the same providers as the map. Hazard coverage varies. Frequencies are percentages, not fractions. No forecasts, binding quotes or payouts are issued. Unknown, blank and duplicate query parameters are rejected. Access controls depend on the deployment; local servers default to loopback."},
            "servers": [{"url": "/"}], "paths": {
                "/api/v1/health": operation("health", "Service health", "Confirms the API is responding; does not contact or verify data providers.", [], "Health"),
                "/api/v1/weather-alerts": operation("weatherAlerts", "Active NWS point alerts", "Resolve NWS point metadata to verify coverage, then query actual active alerts by point, including county and zone products. Return all pages within a bounded page budget; incomplete queries fail. Exclude expired/cancelled products and preserve agency descriptions, instructions, severity and timestamps. Empty verified results mean no returned active alerts, never safety or zero long-term risk. Unverified point coverage returns unavailable. No application cache or automatic retry.", [lat, lon, address], "WeatherAlerts"),
                "/api/v1/flood-zone-context": operation("floodZoneContext", "FEMA mapped flood-zone context", "Query NFHL Flood Hazard Zones layer 28 using point intersection. Retain all intersecting polygons, raw attributes, provider units and vertical datum. Missing numeric codes -9999 and -8888 become null in normalized fields. Base flood elevation is not flood depth. No intersecting polygon returns unavailable because coverage is not established. Truncation or malformed responses fail. Regulatory context does not supply an annual depth curve, property loss estimate or zero-risk conclusion.", [lat, lon, address], "FloodZoneContext"),
                "/api/v1/assessment": operation("assessment", "Evidence assessment by the NDApp agent", "Run the NDApp assessment agent for one point or address: it calls the reviewed MCP tools (hazard curves, FEMA flood zone, USGS elevation and recent earthquakes, wildfire history, NWS alerts, FEMA National Risk Index) under pinned tool definitions, then returns extracted facts with evidence IDs and JSON Pointers, per-hazard evidence status, gaps and evidence provenance hashes. Raw agency payloads are omitted; use the individual routes for them. No AI model is called. assessment_status is complete, partial (some sources failed or had no coverage), unavailable, awaiting_input (ambiguous address) or failed. Typically takes 15-40 s because it queries several agencies. Not a warning, quote, safety determination or loss estimate.", [lat, lon, address,
                    parameter("hazards", {"type": "string", "pattern": "^(flood|earthquake|wildfire)(,(flood|earthquake|wildfire)){0,2}$", "default": "flood,earthquake,wildfire"}, "Comma-separated distinct hazards to assess.", "flood,earthquake", False)], "Assessment"),
                "/api/v1/satellite-fires": operation("satelliteFires", "Satellite active-fire detections nearby", "NASA FIRMS VIIRS 375 m active-fire detections from NOAA-20 and NOAA-21 in the rolling US files (window: about the last 24 hours, 48 hours or 7 days), within radius_km (1-100, default 25) of the point, nearest first, with acquisition time, age, confidence, fire radiative power (MW), day/night and pixel size. frp_summary gives the FRP (MW) minimum, median and maximum over every detection in the radius (not only the 200 returned) and an empirical exceedance curve: the share of those detections at or above each FRP value (up to 50 points). That share is conditional on a detection, counts each overpass of a burning fire separately, and is not an annual probability or fireline intensity. Each source reports its status and file time span; one failed source returns partial results, all failed returns 502. Coverage: contiguous US and Hawaii; search areas outside return unavailable. A detection is a thermal anomaly, not a confirmed wildfire; no detection does not mean no fire. FRP is not fireline intensity. Files are cached for 10 minutes.", [lat, lon, address,
                    parameter("radius_km", {"type": "number", "minimum": 1, "maximum": 100, "default": 25}, "Search radius in km.", 25, False),
                    parameter("window", {"type": "string", "enum": ["24h", "48h", "7d"], "default": "24h"}, "Which rolling FIRMS file to use. Files hold whole UTC days: 24h reaches back about 1–2 days, 48h about 2–3 days, 7d about 7–8 days.", "24h", False)], "SatelliteFires"),
                "/api/v1/elevation-context": operation("elevationContext", "USGS ground elevation", "Query USGS EPQS (3D Elevation Program) for bare-earth elevation at a WGS84 point, in meters, with source resolution and acquisition date when supplied. The response does not state the vertical datum. The no-data value returns unavailable. Elevation is terrain context only: not flood depth, first-floor height or drainage. The upstream service is slow (measured 11-29 s); requests wait up to 30 s, then return 502.", [lat, lon, address], "ElevationContext"),
                "/api/v1/recent-earthquakes": operation("recentEarthquakes", "Recent USGS earthquakes nearby", "Count and list USGS ComCat earthquakes within radius_km (1-300, default 100) over the past days (1-3650, default 365) at or above min_magnitude (0-9.5, default 2.5). Returns the full count and up to the 100 largest events with epicentral great-circle distances. Historical catalog context only: not a ground-shaking probability or hazard curve; catalog completeness varies.", [lat, lon, address,
                    parameter("radius_km", {"type": "number", "minimum": 1, "maximum": 300, "default": 100}, "Search radius in km.", 100, False),
                    parameter("days", {"type": "integer", "minimum": 1, "maximum": 3650, "default": 365}, "Look-back window in days ending now.", 365, False),
                    parameter("min_magnitude", {"type": "number", "minimum": 0, "maximum": 9.5, "default": 2.5}, "Minimum catalog magnitude.", 2.5, False)], "RecentEarthquakes"),
                "/api/v1/risk-index": operation("riskIndex", "FEMA National Risk Index tract context", "Return FEMA National Risk Index attributes for the census tract(s) intersecting a point: composite and 18 per-hazard risk ratings, national percentile scores, expected annual loss in USD, social vulnerability and community resilience ratings, population, building value and index version. These describe the whole tract, are relative rankings rather than probabilities, and are FEMA's own index; NDApp does not combine hazards. No intersecting tract returns unavailable.", [lat, lon, address], "RiskIndexContext"),
                "/api/v1/fire-proximity": operation("fireProximity", "Historical annual nearby-fire frequencies", "Share of published baseline calendar years with at least one FPA FOD reported wildfire within 5, 10 or 20 km of the exact selection. Each year counts once per radius. Only complete published edge years before the current year are included, with at least 20 years required. Zero-record years remain in the denominator under an explicit reporting-coverage assumption; local completeness is not verified. Live WFIGS data is excluded. Returns year-by-year nearest records within 20 km, qualifying years and Wilson 95% binomial reference intervals. size_exceedance combines final reported acres with distance: a burned-area exceedance curve (expected fires per year at least each size, annual probability and return period); yearly frequencies for fires of at least 10, 100, 1,000 and 5,000 acres per radius; years in which an equivalent-circle footprint centered on the reported point covered the selection; and the mean annual share of the 5 and 10 km circles covered by reported footprints. Equivalent circles are approximations of irregular perimeters. Geographic eligibility is checked with FEMA US county polygons; no county coverage returns unavailable rather than zero. Full pagination is required, errors return 502, snapshots cache for up to 5 minutes. This is not future burn probability, intensity exceedance or property loss probability.", [lat, lon, address], "FireProximity"),
                "/api/v1/nearest-fire": operation("nearestFire", "Nearest recorded wildfire — all history", "Search the current-edition USFS FPA FOD archive (currently 1992–2024) and the full NIFC WFIGS wildfire history without a date filter. Both sources must succeed; failed or truncated queries never become a nearest result from partial coverage. Searches expand until the nearest great-circle point is established, with complete pagination. Results cache for up to 5 minutes. Source dates and locations can be approximate; this does not establish property burn extent or current fire activity. No radius or recency filter is imposed. Provider time/page limits return 502 rather than an unverified result.", [lat, lon, address], "NearestFire"),
                "/api/v1/geocode": operation("geocodeAddress", "Resolve a street address", "Check a full US street address before running a hazard calculation. Returns a matched approximate point or unavailable with match_status=no_match/ambiguous. Multiple matches are never selected automatically.", [{**address, "required": True}], "Geocode"),
                "/api/v1/hazards/{peril}": operation("hazardLookup", "Hazard lookup", "Source severity–frequency points, units, provenance and model details. Missing coverage remains explicit. Wind (API key: cyclone) covers tropical cyclones only and reports historical frequency. wildfire uses the nearby reported-fire method: each point is a fire size in acres (severity) and the share of FPA FOD record years with at least one reported wildfire of at least that size within radius_km (any distance above 0 up to 20 km; default 10). Sizes never reported within the radius are left out rather than given 0%. It is a historical frequency, not a forecast, burn probability for the property or loss estimate; /api/v1/fire-proximity has the full detail. Point mode only.", [peril, lat, lon, address, site, radius], "Hazard"),
                "/api/v1/trigger-comparisons/{peril}": operation("triggerComparisons", "Trigger comparisons", "Compare supported point thresholds for one fixed payout. Rows are source levels only, never interpolated. Optional threshold adds custom_trigger, an estimate for any value (log-probability interpolation between levels, exponential-tail extrapolation beyond them), the same as the map's custom threshold. Duplicate rounded thresholds retain a range; alternative rows must not be added together. Contributions rounded to cents, assuming at most one payout per year, excluding fees. wildfire: the trigger is a reported wildfire of at least N acres within radius_km in a calendar year, priced on the observed share of record years (frequency_basis=historical_annual_frequency), the same as the map.", [peril, lat, lon, address, site, radius, payout, threshold], "TriggerComparisons"),
                "/api/v1/areas/{peril}": operation("areaAnalysis", "Area analysis", "Samples a fixed 5×5 grid including boundaries and center. Averages available probabilities at each severity, with interpolation inside valid brackets. Coverage may differ by threshold. This is not the probability of an event anywhere in the area. wildfire instead returns the nearby reported-fire curve at the area's center (as the map does), using radius_km. Requests may be slow because they sample 25 points.", [peril, *bounds, site, radius], "Area"),
                "/api/v1/monitoring-stations": operation("monitoringStations", "Nearest monitoring stations", "Search unique METAR weather stations from the worldwide NOAA AWC catalog within 200 km. Omit peril; network defaults to awc_metar and n defaults to 5. Distances are great-circle km, sorted locally, not provider-ordered. Latest reports include units and UTC times; older than 2 hours is stale. observation.heat adds a derived shaded Heat Index snapshot in Celsius/Fahrenheit with source-based screening bands; missing, stale, future or out-of-envelope data withholds HI. It is not WBGT, a forecast, an annual probability or a construction stop-work rule. Missing/failed observations do not hide station links. Fewer results are explicit. Hazard stations: supply peril with n (1-50) for the nearest n unique stations in that hazard's own network (flood: USGS stream gauges; rainfall: USGS precipitation gauges; earthquake: EarthScope seismic stations; wildfire: NIFC RAWS; cyclone: METAR wind stations), metadata only. Legacy single-station clients may supply peril without n. peril cannot be combined with network. Stations do not change hazard curves or contributions.", [parameter("network", {"type": "string", "enum": ["awc_metar"]}, "Multi-station network; omit peril.", "awc_metar", False), parameter("n", {"type": "integer", "minimum": 1, "maximum": 50, "default": 5}, "Positive integer result count; omit peril.", 5, False), parameter("peril", {"type": "string", "enum": sorted(STATION_PERILS)}, "Hazard network. With n: nearest n hazard stations; without n: legacy single station. Omit network.", required=False), lat, lon, address], "Stations"),
            }, "components": {"schemas": schemas}}
