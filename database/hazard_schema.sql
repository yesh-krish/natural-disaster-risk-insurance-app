-- PostgreSQL 15+ / PostGIS architecture blueprint; not applied by NDApp.
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE insured_location (
    id uuid PRIMARY KEY,
    point geometry(Point, 4326) NOT NULL,
    coordinate_accuracy_m double precision CHECK (coordinate_accuracy_m >= 0),
    ground_elevation_m double precision,
    vertical_datum text,
    footprint geometry(Polygon, 4326),
    CHECK (ST_X(point) BETWEEN -180 AND 180 AND ST_Y(point) BETWEEN -90 AND 90)
);
CREATE INDEX insured_location_point_idx ON insured_location USING gist(point);

CREATE TABLE source_artifact (
    id uuid PRIMARY KEY,
    provider text NOT NULL,
    product text NOT NULL,
    product_version text NOT NULL,
    request_url text NOT NULL,
    fetched_at timestamptz NOT NULL,
    sha256 char(64) NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
    object_uri text NOT NULL, -- immutable raw JSON/GeoTIFF/GRIB/perimeter in object storage
    license_metadata jsonb NOT NULL DEFAULT '{}',
    UNIQUE (provider, product, product_version, sha256)
);

CREATE TABLE metric_definition (
    id text PRIMARY KEY, -- immutable versioned ID
    metric text NOT NULL CHECK (metric IN
      ('pga_pct_g','rain_24h_mm','wind_gust_kn','inundation_m','flame_length_m')),
    canonical_unit text NOT NULL,
    definition jsonb NOT NULL, -- component, duration, height, terrain, datum, spatial assignment
    CHECK ((metric = 'pga_pct_g' AND canonical_unit = '%g') OR
           (metric = 'rain_24h_mm' AND canonical_unit = 'mm') OR
           (metric = 'wind_gust_kn' AND canonical_unit = 'kn') OR
           (metric IN ('inundation_m','flame_length_m') AND canonical_unit = 'm'))
);

CREATE TABLE hazard_model (
    id uuid PRIMARY KEY,
    location_id uuid NOT NULL REFERENCES insured_location,
    peril text NOT NULL CHECK (peril IN ('earthquake','rain_wind','flood','wildfire')),
    model_version text NOT NULL,
    metric_definition_ids text[] NOT NULL, -- service validates each against metric_definition
    frequency_measure text NOT NULL CHECK (frequency_measure IN ('annual_rate','annual_probability')),
    reference_years numeric NOT NULL DEFAULT 1 CHECK (reference_years = 1),
    representation text NOT NULL CHECK (representation IN ('curve','surface','event_catalog')),
    occurrence_assumption text NOT NULL,
    calibration_period daterange,
    scenario_year integer,
    scenario_name text NOT NULL,
    parameters jsonb NOT NULL, -- Vs30/class, datum, hydraulic transfer, copula, FWI coefficients
    uncertainty jsonb NOT NULL, -- bootstrap/logic-tree draws + quantile semantics
    coverage_status text NOT NULL CHECK (coverage_status IN ('available','unavailable')),
    approval_status text NOT NULL CHECK (approval_status IN ('research_only','approved','retired')),
    created_at timestamptz NOT NULL,
    UNIQUE (id, location_id, peril)
);
CREATE TABLE model_artifact (
    model_id uuid NOT NULL REFERENCES hazard_model,
    artifact_id uuid NOT NULL REFERENCES source_artifact,
    role text NOT NULL, -- curve / BP / conditional_intensity / DEM / weather / calibration
    PRIMARY KEY (model_id, artifact_id, role)
);
CREATE TABLE hazard_curve_node (
    model_id uuid NOT NULL REFERENCES hazard_model,
    intensity numeric NOT NULL CHECK (intensity >= 0),
    frequency numeric NOT NULL CHECK (frequency >= 0),
    aep numeric NOT NULL CHECK (aep BETWEEN 0 AND 1),
    PRIMARY KEY (model_id, intensity)
    -- Ingest validates monotonicity and AEP/frequency consistency against model's measure.
);
CREATE TABLE hazard_surface_node (
    model_id uuid NOT NULL REFERENCES hazard_model,
    rain_24h_mm numeric NOT NULL CHECK (rain_24h_mm >= 0),
    wind_gust_kn numeric NOT NULL CHECK (wind_gust_kn >= 0),
    rule_operator text NOT NULL CHECK (rule_operator IN ('AND','OR')),
    aep numeric CHECK (aep BETWEEN 0 AND 1), -- NULL = unsupported tail
    PRIMARY KEY (model_id, rain_24h_mm, wind_gust_kn, rule_operator)
);
CREATE TABLE event_catalog_row (
    model_id uuid NOT NULL REFERENCES hazard_model,
    canonical_event_id text NOT NULL,
    annual_rate numeric NOT NULL CHECK (annual_rate >= 0),
    intensities jsonb NOT NULL, -- paired same-site, same-storm maxima in canonical units
    event_metadata jsonb NOT NULL,
    PRIMARY KEY (model_id, canonical_event_id)
);

CREATE TABLE monitoring_assignment (
    id uuid PRIMARY KEY,
    location_id uuid NOT NULL REFERENCES insured_location,
    metric_definition_id text NOT NULL REFERENCES metric_definition,
    source_id text NOT NULL,
    network_station_id text,
    valid_during tstzrange NOT NULL,
    transfer_model_artifact_id uuid REFERENCES source_artifact,
    assignment_metadata jsonb NOT NULL -- river reach/basin, datum transform, distance, representativeness
);
CREATE TABLE environmental_event (
    id text PRIMARY KEY, -- canonical cross-provider identity
    peril text NOT NULL,
    event_window tstzrange NOT NULL,
    event_definition_id text NOT NULL,
    metadata jsonb NOT NULL
);
CREATE TABLE observation (
    id uuid PRIMARY KEY,
    assignment_id uuid NOT NULL REFERENCES monitoring_assignment,
    event_id text NOT NULL REFERENCES environmental_event,
    metric_definition_id text NOT NULL REFERENCES metric_definition,
    source_artifact_id uuid NOT NULL REFERENCES source_artifact,
    window_start timestamptz NOT NULL,
    window_end timestamptz NOT NULL,
    issued_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL,
    normalized_value numeric, -- NULL = missing; never coalesce NULL to zero
    quality text NOT NULL CHECK (quality IN ('accepted','provisional','rejected','missing')),
    finalized boolean NOT NULL DEFAULT false,
    revision text NOT NULL,
    UNIQUE (assignment_id, event_id, metric_definition_id, window_start, window_end, revision),
    CHECK (window_start <= window_end AND window_end <= issued_at AND issued_at <= received_at)
);

CREATE TABLE policy_term (
    id uuid PRIMARY KEY,
    location_id uuid NOT NULL REFERENCES insured_location,
    peril text NOT NULL,
    model_id uuid NOT NULL,
    coverage tstzrange NOT NULL,
    currency char(3) NOT NULL,
    annual_limit_minor bigint NOT NULL CHECK (annual_limit_minor > 0),
    contract_basis text NOT NULL CHECK (contract_basis = 'annual_maximum_top_up'),
    terms jsonb NOT NULL, -- immutable nested tiers, >=, AND/OR, approved assignments, event rule, finality
    terms_sha256 char(64) NOT NULL CHECK (terms_sha256 ~ '^[a-f0-9]{64}$'),
    FOREIGN KEY (model_id, location_id, peril) REFERENCES hazard_model(id, location_id, peril)
);
CREATE TABLE benchmark_quote (
    id uuid PRIMARY KEY,
    model_id uuid NOT NULL REFERENCES hazard_model,
    policy_term_id uuid REFERENCES policy_term,
    calculated_at timestamptz NOT NULL,
    currency char(3) NOT NULL,
    expected_payout_minor numeric NOT NULL CHECK (expected_payout_minor >= 0),
    premium_minor bigint NOT NULL CHECK (premium_minor >= 0),
    inputs jsonb NOT NULL,
    outputs jsonb NOT NULL, -- tier AEPs, loads, model/contract IDs, annual basis
    engine_version text NOT NULL
);
CREATE TABLE annual_payout_balance (
    policy_term_id uuid PRIMARY KEY REFERENCES policy_term,
    paid_or_reserved_minor bigint NOT NULL DEFAULT 0 CHECK (paid_or_reserved_minor >= 0)
);
CREATE TABLE trigger_decision (
    id uuid PRIMARY KEY,
    policy_term_id uuid NOT NULL REFERENCES policy_term,
    event_id text NOT NULL REFERENCES environmental_event,
    evidence_revision_sha256 char(64) NOT NULL,
    evaluated_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('eligible','no_payment_due','insufficient_data')),
    payout_due_minor bigint NOT NULL CHECK (payout_due_minor >= 0),
    observation_ids uuid[] NOT NULL,
    reasoning jsonb NOT NULL,
    UNIQUE (policy_term_id, event_id, evidence_revision_sha256)
);
CREATE TABLE payout_outbox (
    id uuid PRIMARY KEY,
    decision_id uuid NOT NULL UNIQUE REFERENCES trigger_decision,
    idempotency_key text NOT NULL UNIQUE,
    amount_minor bigint NOT NULL CHECK (amount_minor > 0),
    state text NOT NULL CHECK (state IN ('reserved','submitted','settled','failed')),
    payment_provider_reference text UNIQUE,
    created_at timestamptz NOT NULL
);

-- Settlement transaction (implemented by a trusted service, not a browser):
-- 1. BEGIN; SELECT annual_payout_balance ... FOR UPDATE for policy_term_id.
-- 2. Load immutable policy and canonical finalized observations from storage.
-- 3. Evaluate using paid_or_reserved_minor; insert unique trigger_decision.
-- 4. For eligible decision: increase balance within annual_limit_minor; insert outbox.
-- 5. COMMIT. Durable worker submits with outbox idempotency_key and reconciles receipts.
-- Reserved balance is held across retries. Release only after definitive nonpayment.
-- Enforce immutability of bound terms, model snapshots, artifacts and definitions via
-- service/database roles; allow append-only revisions. No UPDATE permission for clients.
