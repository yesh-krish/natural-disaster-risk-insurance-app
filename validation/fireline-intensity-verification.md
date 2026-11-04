# Fireline-intensity integration verification

Verified October 29, 2026 (America/Chicago).

Story: choose the fireline-intensity hazard and a map point/address or rectangle → local research API → native Texas A&M Forest Service / TxWRAP raster → kW/m result with source/provenance and CSV export.

| Boundary | Evidence |
| --- | --- |
| Provider and scientific definition | Live service metadata advertises F32, one band, weighted-average heading-only fireline intensity in kilowatts per meter. No rendering conversion is applied. |
| API → live data | `verify_fireline_api.py` passed native positive, native zero, outside-coverage NoData, 25-sample spatial mean, and legacy/v1 route checks. Saved responses: `fireline-intensity-live.json`. |
| Probability exclusion | Native result has empty curve points and `annual_probability_available=false`. Trigger comparison returns unavailable with no contribution rows. Offline tests also reject stray probability points for this hazard. |
| Browser → API → UI | Actual address search selected a public San Antonio address, and a subsequent map click at approximately 29.642053, −98.632722 displayed **1,882.4 kW/m** from the native raster. Browser error log was empty at the final check. |
| Browser → CSV | The actual downloaded point CSV retained **1882.431640625 kW/m**, the correct statistic, source URL, sample retrieval time, metadata/sample SHA256, and `annual_probability_available=false`. Saved as `fireline-intensity-browser.csv`. |
| Rectangle UI and switching | The application DOM/map harness verified per-sample values, partial coverage, average/samples CSV, probability-control exclusion, clearing stale results, and switching back to curve hazards. Real rectangle averaging was verified separately through the live API. |
| Regression checks | All **93 Python tests** and all **4 JavaScript test entry points** passed. |

Preview: `fireline-intensity-card.jpg`. Full page capture: `fireline-intensity-preview.jpg` (WebGL basemap rendering can be incomplete in a full-document capture).

This verifies the integration and source contracts. It is not validation of modeled fire behavior against observed fire intensity, a property loss model, or insurance calibration. The source is a Texas scenario statistic; it does not establish an annual intensity exceedance curve. The underlying service has no immutable model version in its metadata; hashes/timestamps preserve what was retrieved.
