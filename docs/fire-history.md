# Nearest recorded wildfire

Choose **Wildfire / Nearby fire frequency** in the hazard dropdown, then select
a point on the map (or resolve an address) to show the closest dated,
reported US wildfire location across **all available history in both connected
sources**. There is no recency filter. An older nearby fire can rank ahead of a
new fire farther away. The red marker identifies the reported fire point; a
dashed line connects it to the selection. **Show selection and fire** fits both.
Rectangular selections use their center, explicitly labeled in the result.
Selecting this hazard uses the current selection immediately. Switching to
another hazard clears the fire marker and cancels its requests and periodic
checks. The lookup panel appears only for this hazard. Annual probability,
trigger and payout controls are hidden; a separate annual-frequency panel
describes historical reported-fire proximity.

## Annual nearby-fire frequency

The wildfire view also calculates **the share of published archive years with
at least one reported wildfire within 5, 10 and 20 km of the selection**. This is
option 1 from the fire-risk exploration: it uses every eligible fire size, and
does not implement a fire-size threshold or a property-loss model.

For radius r, frequency = 100 × number of qualifying years / baseline years.
Each calendar year counts once per radius, regardless of the number of fires.
Radii are nested, so their frequencies are not additive. Larger radii cannot
have fewer qualifying years. The graph uses frequency on X and search radius
on Y; it is a proximity distribution, not an intensity-exceedance curve.

The baseline comes **only from the current published FPA FOD edition**, currently
1992–2024, or 33 calendar years. Archive time-extent metadata sets the period;
partial edge years and the current calendar year are excluded, and at least
20 years are required. WFIGS records are not mixed into these frequencies:
operational updates and unfinished/recent years remain nearest-fire context.
The current-edition endpoint is checked every five minutes and can extend the
baseline when a new archive edition is published.

**Coverage assumption:** all calendar years in that published period form the
denominator. If no eligible archive record is present within a radius in a year,
that year contributes zero to the numerator. This describes the archive's
reported occurrences; local reporting completeness has not been independently
verified. Missing reports, changing agency participation, fuels and climate can
change interpretation. No source-completeness audit or climate adjustment is
performed, and zero reported years does not establish zero future risk.

A FEMA NRI county polygon query checks that the selected point is within mapped
US county coverage before producing a frequency. Outside coverage returns
`unavailable`, not zeros. Boundary-adjacent points still use US reports only;
fires in neighboring countries can be omitted. Failure to check coverage or
retrieve all pages returns HTTP 502. Invalid/undated returned records withhold
the calculation rather than silently making an annual zero. Identical archive
IDs are deduplicated; inconsistent duplicate records cause a retryable failure.

The table includes the numerator, denominator, exact frequency in the API/CSV,
and a **Wilson 95% binomial reference interval**. The interval assumes independent,
comparable annual trials; it does not include reporting, geolocation or climate
uncertainty. It is not a forecast confidence interval. No probabilities are
extrapolated below the empirical resolution of the record.

Select 5, 10 or 20 km to highlight a table row and draw a blue radius circle.
**Show selected radius** fits that circle. **Inspect each baseline year** shows
the nearest eligible fire *within 20 km*, the date and each radius flag. A blank
distance means no eligible record within the search circle; the nearest fire
globally for that year was not calculated. Year rows, source record URLs,
coordinates, baseline, radius flags, summary frequencies, intervals and retrieval
time are included in **Export annual frequencies and years CSV**. Spreadsheet
formula prefixes in provider-supplied text are neutralized in the CSV export.

The frequency result and the all-history nearest-fire result load independently:
one failing does not hide a successful result from the other. Each clears its
own stale state, supports refresh, and cancels requests and timers when switching
to another hazard or waiting for an address match. Rectangular selections use
the center rather than an area-average or anywhere-in-area probability.

```text
GET /api/v1/fire-proximity?lat=29.4241&lon=-98.4936
GET /api/v1/fire-proximity?address=100%20Dolorosa%20St%2C%20San%20Antonio%2C%20TX
```

The v1 API returns `frequency_basis=historical_annual_reported_fire_frequency`,
`baseline`, `frequencies`, `annual_records`, source/coverage provenance and
`checked_at`. It accepts the same strict coordinate/address inputs as nearest
fire, and uses a bounded five-minute cache for the exact selected coordinates.

## Sources and coverage

- [USFS FPA FOD current-edition layer](https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_FireOccurrenceCurrentEdition_01/MapServer/0):
  Seventh Edition, approximately 2.66 million US wildfire records for 1992–2024.
  The stable endpoint receives new editions when published, rather than daily
  incident updates. Reported discovery dates, final size and point locations are
  required by this archive. Location precision can be as coarse as a one-square-mile
  PLSS section. The archived publication is [RDS-2013-0009.7](https://doi.org/10.2737/RDS-2013-0009.7).
- [NIFC WFIGS full incident history](https://www.arcgis.com/home/item.html?id=b4402f7887ca4ea9a6189443f220ef28):
  operational IRWIN wildfire records, nominally from the 2014 implementation
  onward, with some older entries. Its documented refresh interval is five
  minutes. The lookup uses the full history, including completed incidents,
  rather than the current-incidents service with fall-off rules. It filters
  `IncidentTypeCategory='WF'`, `IsValid=1`, and unquarantined records, excluding
  prescribed burns and complex summary records.

Schema and coverage were checked against both public services on November 4,
2026 (America/Chicago). Live statistics returned 2,661,383 dated FPA records
from 1992 through 2024, and 392,139 valid WFIGS wildfire records, with a latest
discovery on November 4, 2026. Those counts will change. No bulk download,
credentials or additional package is needed.

"All available history" means all eligible records in these two datasets, not
an exhaustive inventory of every fire ever. Earlier fires, missing agency
reports, undated records and invalid/future records are not represented in the
result. Some incidents occur in both sources. The lookup ranks reported points,
not unique incident counts; overlapping records do not affect the minimum
distance, although their reported locations can differ. Ties use source and ID
for deterministic selection. The displayed source identifies the chosen record.

## Refresh and search

The browser searches on selection and checks again every five minutes while
visible. Returning to the page checks again. **Refresh fire history** retries a
failed request or requests the latest server snapshot. The server caches each
exact input point for up to five minutes. Last checked time is source retrieval
time, not the fire discovery time or a guarantee of real-time agency reporting.
Provider failure clears the displayed result and offers refresh; a previous fire
is not left on the map as though its nearest status had just been verified.

Search starts at 2 km and expands through 10, 50, 250, 1,250, 5,000 and 10,000 km,
then the whole globe if needed. Each radius is queried against **both** sources.
Conservative bounding boxes enclose the spherical circle, splitting at the date
line and handling poles. All pages are fetched with stable object-ID ordering.
Distances are calculated from the exact selected coordinates using a spherical
great-circle distance. Bounding-box corners outside the radius are excluded.
Once a candidate lies inside a completely searched circle, points outside the
circle cannot beat it; all candidates within it are ranked without rounding.

Both providers must succeed before returning a nearest result. ArcGIS error
objects, repeated/empty truncated pages, timeout (45-second search budget), and
excessive paging (100 pages per envelope per provider) produce an explicit HTTP
502. There is no silent radius cutoff or partial-source fallback. Source edits
during pagination can affect a snapshot; this is not a transactional archive.

## API

```text
GET /api/v1/nearest-fire?lat=29.4241&lon=-98.4936
GET /api/v1/nearest-fire?address=100%20Dolorosa%20St%2C%20San%20Antonio%2C%20TX
```

Inputs follow the existing coordinate/address validation. Unknown, blank,
duplicate, nonfinite or mixed location inputs return 400; unresolved addresses
return 422. Successful responses include `time_window=all_available_history`,
`fire`, both `sources`, `checked_at`, `refresh_seconds`, and the searched radius.
The fire includes name, ID, coordinates, unrounded distance, discovery date,
reported acres where available, source and a direct record query link.

This is a historical occurrence lookup, separate from hazard curves and pool
contribution calculations. A reported point does not establish the fire's
perimeter, that the selected property burned, or that a historical fire is still
active. Source omissions never establish that no fire occurred.
