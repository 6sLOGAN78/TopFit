# Config

Every YAML file here is baked into the Docker image (`COPY config ./config`,
`CONFIG_DIR=/app/config`) and loaded into typed Pydantic models by
`ranker.config_loader.load_config`.

Files added by later plans:

- `sources.yaml` - enabled sources, schedules, board lists
- `cities.yaml` - city aliases to canonical India locations
- `regions.yaml` - region/country mapping
- `scope.yaml` - tech-role scope filter
- `fx.yaml` - fixed FX rates for salary normalisation
- `skills.yaml` - skill taxonomy and aliases
- `llm_prices.yaml` - per-model token prices for cost accounting
- `ranking.yaml` - search retrieval parameters (K values, RRF constant, vector mode); any
  change alters the `pipeline_version` stored in `search_logs`
