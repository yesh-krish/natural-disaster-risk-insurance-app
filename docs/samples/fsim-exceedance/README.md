# FSim exceedance research evidence

Retrieved November 3, 2026. These are documentation and package-inventory samples, not new application data or a calibrated fireline-intensity hazard curve.

- `source-manifest.json` records source URLs, UTC retrieval times, byte lengths and SHA256 for saved agency HTML.
- `fsim-2023-metadata.html` and `fsim-2023-file-index.html` document the national third-edition BP/FLP dataset already used by the app.
- `fsim-2025-catalog.html`, `fsim-2025-metadata.html` and `fsim-2025-file-index.html` document the newer CONUS climate runs and the event-data availability statement.
- `wrc-2024-catalog.html` documents the national WRC product family and its distinct BP/intensity modeling.
- `fsim-2025-archive-inventory.json` records the ZIP directory obtained from the official catalog's Box link using HTTP ranges. Only 19,075 bytes were read from the 1,709,275,509-byte archive. The inventory has 150 entries and 14 `.tif` entries: BP and FLP1–6 for each climate. This does not verify the full ZIP's checksum, individual raster CRCs, or scientific values.

The package inventory lacks native intensity or event/arrival-time rasters. That observation does not disprove availability of separate event data described in the metadata. Contacting the producer would be a separate action; no correspondence was sent. No FSim/ELMFIRE installation or simulation was performed.

See [the research assessment](../../fsim-fireline-exceedance.md) for interpretation, probability equations and outstanding requirements.
