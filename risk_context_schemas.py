"""Shared JSON Schemas for API and agent-tool context results."""


def context_schemas():
    text = {"type": ["string", "null"]}
    timestamp = {"type": "string", "format": "date-time"}
    base = {"status": {"enum": ["available", "not_found", "unavailable"]},
            "latitude": {"type": "number", "minimum": -90, "maximum": 90},
            "longitude": {"type": "number", "minimum": -180, "maximum": 180},
            "source_name": {"type": "string"}, "source_url": {"type": "string", "format": "uri"},
            "checked_at": timestamp, "notes": {"type": "array", "items": {"type": "string"}},
            "reason": text}
    alert_fields = {key: text for key in ("headline", "onset", "ends", "description", "instruction", "response")}
    alert_fields.update({key: {"type": "string"} for key in
                         ("id", "event", "severity", "certainty", "urgency", "areaDesc")})
    alert_fields.update({key: timestamp for key in ("sent", "effective", "expires")})
    alert_fields["source_url"] = {"type": "string", "format": "uri"}
    zone_fields = {"zone": {"type": "string", "minLength": 1}, "subtype": text,
                   "special_flood_hazard_area": {"type": ["boolean", "null"]},
                   "static_base_flood_elevation": {"type": ["number", "null"]},
                   "mapped_depth": {"type": ["number", "null"]},
                   "length_unit": text, "vertical_datum": text,
                   "raw_attributes": {"type": "object"}}
    number = {"type": ["number", "null"]}
    nri_rating = {"type": "object", "required": ["risk_rating", "risk_score", "eal_rating", "expected_annual_loss_usd"],
                  "properties": {"risk_rating": text, "risk_score": number, "eal_rating": text,
                                 "expected_annual_loss_usd": number}}
    event_fields = {"id": {"type": "string"}, "magnitude": {"type": "number"}, "magnitude_type": text,
                    "place": text, "time": timestamp, "latitude": {"type": "number"},
                    "longitude": {"type": "number"}, "depth_km": number,
                    "epicentral_distance_km": {"type": "number", "minimum": 0}, "review_status": text,
                    "max_mmi": number, "pager_alert": text, "tsunami_flag": {"type": "boolean"},
                    "source_url": {"type": ["string", "null"]}}
    detection_fields = {"satellite": {"enum": ["NOAA-20", "NOAA-21"]}, "instrument": {"const": "VIIRS"},
                        "latitude": {"type": "number"}, "longitude": {"type": "number"},
                        "distance_km": {"type": "number", "minimum": 0}, "acquired_at": timestamp,
                        "age_hours": {"type": "number", "minimum": 0},
                        "confidence": {"enum": ["low", "nominal", "high"]}, "frp_mw": number,
                        "daynight": {"enum": ["day", "night", None]}, "pixel_scan_km": number,
                        "pixel_track_km": number, "brightness_ti4_k": number, "version": text}
    satellite_source = {"type": "object", "required": ["satellite", "status", "url"], "properties": {
        "satellite": {"type": "string"}, "status": {"enum": ["ok", "failed"]}, "url": {"type": "string", "format": "uri"},
        "retrieved_at": timestamp, "file_detection_count": {"type": "integer", "minimum": 0},
        "oldest_acquired_at": text, "newest_acquired_at": text, "error": text}}
    return {
        "SatelliteFires": {"type": "object", "required": [*base.keys(), "radius_km", "window", "window_description",
                                                          "detection_count", "returned_count", "detections", "sources"],
                           "properties": {**base, "radius_km": {"type": "number", "minimum": 1, "maximum": 100},
                               "window": {"enum": ["24h", "48h", "7d"]}, "window_description": {"type": "string"},
                               "detection_count": {"type": "integer", "minimum": 0},
                               "returned_count": {"type": "integer", "minimum": 0, "maximum": 200},
                               "nearest_km": number, "newest_age_hours": number,
                               "sources": {"type": "array", "items": satellite_source},
                               "detections": {"type": "array", "maxItems": 200, "items": {
                                   "type": "object", "required": list(detection_fields), "properties": detection_fields}}}},
        "ElevationContext": {"type": "object", "required": [*base.keys(), "elevation_m", "raw_value",
                                                            "source_resolution", "acquisition_date"],
                             "properties": {**base, "status": {"enum": ["available", "unavailable"]},
                                 "elevation_m": number, "raw_value": {"type": "string"},
                                 "source_resolution": number, "acquisition_date": text}},
        "RecentEarthquakes": {"type": "object", "required": [*base.keys(), "window", "total_count", "returned_count",
                                                             "truncated", "largest_magnitude", "events"],
                              "properties": {**base, "status": {"enum": ["available", "not_found"]},
                                  "window": {"type": "object", "required": ["start", "end", "days", "radius_km", "min_magnitude"],
                                             "properties": {"start": timestamp, "end": timestamp,
                                                            "days": {"type": "integer"}, "radius_km": {"type": "number"},
                                                            "min_magnitude": {"type": "number"}}},
                                  "total_count": {"type": "integer", "minimum": 0},
                                  "returned_count": {"type": "integer", "minimum": 0, "maximum": 100},
                                  "truncated": {"type": "boolean"}, "largest_magnitude": number,
                                  "events": {"type": "array", "maxItems": 100, "items": {
                                      "type": "object", "required": list(event_fields), "properties": event_fields}}}},
        "RiskIndexContext": {"type": "object", "required": [*base.keys(), "tract_count", "tracts"],
                             "properties": {**base, "status": {"enum": ["available", "unavailable"]},
                                 "tract_count": {"type": "integer", "minimum": 0},
                                 "tracts": {"type": "array", "maxItems": 10, "items": {
                                     "type": "object",
                                     "required": ["tract_fips", "state", "county", "nri_version", "population",
                                                  "building_value_usd", "composite", "hazards"],
                                     "properties": {"tract_fips": {"type": "string"}, "state": text, "county": text,
                                         "nri_version": text, "population": number, "building_value_usd": number,
                                         "composite": {"type": "object", "properties": {
                                             **nri_rating["properties"], "social_vulnerability_rating": text,
                                             "community_resilience_rating": text}},
                                         "hazards": {"type": "object", "additionalProperties": nri_rating}}}}}},
        "WeatherAlerts": {"type": "object", "required": [key for key in base if key != "reason"] + [
                           "coverage_verified", "alert_count", "alerts", "source_updated_at"],
                          "properties": {**base, "coverage_verified": {"type": "boolean"},
                              "alert_count": {"type": "integer", "minimum": 0},
                              "excluded_expired_or_cancelled": {"type": "integer", "minimum": 0},
                              "source_updated_at": text,
                              "alerts": {"type": "array", "items": {"type": "object",
                                         "required": list(alert_fields), "properties": alert_fields}}}},
        "FloodZoneContext": {"type": "object", "required": [*base.keys(), "zone_count", "zones"],
                             "properties": {**base, "status": {"enum": ["available", "unavailable"]},
                                 "zone_count": {"type": "integer", "minimum": 0},
                                 "zones": {"type": "array", "maxItems": 100,
                                           "items": {"type": "object", "required": list(zone_fields),
                                                     "properties": zone_fields}}}},
    }
