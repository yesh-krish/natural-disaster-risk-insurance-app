# San Antonio ten-point data check

For the October 29 native fireline-intensity work, see
[integration verification](fireline-intensity-verification.md),
[live source/API responses](fireline-intensity-live.json), and
[browser-exported CSV](fireline-intensity-browser.csv).

`san_antonio_points.csv` records a reproducible check of the local TWDB depth lookup and FEMA NFHL point query. Run `.\.venv\Scripts\python.exe validate_points.py` from the project root. The script uses random seed `20261007` within latitude 29.37–29.49 and longitude -98.57–-98.45. It deliberately chooses five cells with positive TWDB 1% annual-chance depth and five blank cells, separated by at least about 0.006 degrees within each group. It samples all four TWDB events and queries FEMA NFHL layer 28 for `FLD_ZONE`, `ZONE_SUBTY`, and `SFHA_TF`.

On 2026-10-07, all ten FEMA queries returned Zone X with `SFHA_TF=F`. The five positive TWDB 1% cells therefore differ from the FEMA Special Flood Hazard Area flag; the five blank TWDB cells have the same binary classification. One transient FEMA connection error was retried separately and its successful result recorded in the CSV. A future rerun may encounter temporary service errors, which the script records instead of inventing FEMA values.

This is **not an accuracy score**. The sample was stratified on TWDB's 1% result, so it is not representative of San Antonio. TWDB's 2025 combined-peril 3 m modeled depths and FEMA's regulatory flood-zone polygons have different methods and purposes. Zone X does not prove zero flood risk, and blank/zero TWDB cells may mean NoData or no associated modeled risk. No observed flood events, measured water depths, or building damage were used for ground-truth validation.

Sources: [TWDB 2025 Cursory Floodplain Dataset](https://twdb-flood-planning-resources-twdb.hub.arcgis.com/pages/cursory-floodplain-data-2025), [TWDB data structure guide](https://s3.amazonaws.com/twdb-gis-data/Staged/office-of-planning/flood-planning/flood-data/flood-risk/cursory-floodplain-data-2025/Cursory_Floodplain_Data_Structure_Phase_2.pdf), [FEMA NFHL MapServer](https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28).
