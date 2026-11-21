# Phase 2: Live Jobs Index - Pattern Map

**Mapped:** 2026-11-21
**Files analyzed:** 52 (new + modified)
**Analogs found:** 31 / 52 (the codebase is a Phase 1 walking skeleton of ~1,000 lines; the domain modules have no precedent and fall back to RESEARCH.md patterns)

All paths are relative to the repo root. `backend/` is the Docker build context, the uv project root and the pytest root.

## File Classification

### Modified files

| File | Role | Data Flow | Closest Analog | Match Quality |
|------|------|-----------|----------------|---------------|
| `backend/pyproject.toml` | config | — | itself (deps, `[project.scripts]`, pytest markers) | exact |
| `backend/Dockerfile` | config | — | itself (`COPY ranker ./ranker` lines 931-933 of the runtime stage) | exact |
| `backend/ranker/settings.py` | config | — | itself | exact |
| `.env.example` | config | — | itself (sections + `tests/test_env_example.py` guard) | exact |
| `backend/ranker/observability.py` | utility | — | itself (`REDACT_KEYS`, `configure_logging` logger levels) | exact |
| `backend/ranker/worker/__main__.py` | service (process entry) | event-driven | itself (stub with signal handling) | exact |
| `backend/ranker/api/__init__.py` | route (router registry) | request-response | itself | exact |
| `backend/migrations/env.py` | migration config | — | itself (`target_metadata = None` → `Base.metadata`) | exact |
| `backend/ranker/selfcheck.py` | utility (CI image check) | batch | itself (extend to load every config YAML) | exact |
| `backend/ranker/embeddings/embedder.py` (+ `embeddings/__init__.py`) | service | batch | itself (add `embed_missing()` service nearby or in `embeddings/service.py`) | exact |
| `deploy/compose.prod.yaml` | config | — | itself (`worker` service: add `mem_limit`, keep single replica) | exact |
| `backend/tests/conftest.py` | test fixture | — | itself (add async engine / per-test cleanup fixtures) | exact |
| `backend/tests/test_worker.py` | test | event-driven | itself (subprocess + signal test) | exact |

### New files

| File | Role | Data Flow | Closest Analog | Match Quality |
|------|------|-----------|----------------|---------------|
| `backend/migrations/versions/0002_jobs_index.py` | migration | — | `backend/migrations/versions/0001_baseline_vector.py` | exact |
| `backend/ranker/db/models.py` | model | CRUD | none (no ORM yet); naming from `db/engine.py` | no analog |
| `backend/ranker/db/__init__.py` (re-export `Base`) | barrel | — | `backend/ranker/db/__init__.py` | exact |
| `backend/config/{sources,cities,regions,scope,fx,skills,llm_prices}.yaml` | config data | file-I/O | none | no analog |
| `backend/ranker/config_loader.py` | utility | file-I/O | `backend/ranker/settings.py` (Pydantic + `lru_cache`) | role-match |
| `backend/ranker/cli.py` | controller (CLI) | request-response | `backend/ranker/selfcheck.py` (`main() -> int` exit code), `worker/__main__.py` (`asyncio.run`) | partial |
| `backend/ranker/ingest/sources/base.py` (`RawJob`, `SourceAdapter` Protocol) | model + interface | streaming | `backend/ranker/embeddings/embedder.py` (Protocol + impl + fake) | role-match |
| `backend/ranker/ingest/sources/registry.py` / `sources/__init__.py` | utility (registry) | — | `backend/ranker/embeddings/embedder.py::build_embedder` + `embeddings/__init__.py` barrel | partial |
| `backend/ranker/ingest/sources/remotive.py` | service (adapter) | streaming (HTTP pull) | none (no outbound HTTP yet) | no analog |
| `backend/ranker/ingest/sources/adzuna.py` | service (adapter) | streaming (paged HTTP) | none | no analog |
| `backend/ranker/ingest/sources/greenhouse.py` (conditional) | service (adapter) | streaming | none | no analog |
| `backend/ranker/ingest/sources/lever.py` (conditional) | service (adapter) | streaming | none | no analog |
| `backend/ranker/ingest/http.py` | utility | request-response | `ranker/api/health.py` error-type-only pattern; `observability._scrub` | partial |
| `backend/ranker/ingest/normalize/{html,location,region,salary,seniority,company,scope}.py` | utility (pure) | transform | none (pure functions) | no analog |
| `backend/ranker/ingest/skills.py` | utility (pure) | transform | `observability.py` module-level compiled regex (`EMAIL_RE`) | partial |
| `backend/ranker/ingest/hashing.py` | utility (pure) | transform | `embedder.FakeEmbedder` (hashlib sha256) | partial |
| `backend/ranker/ingest/expiry.py` | service | batch (SQL set ops) | `ranker/api/health.py` (`text()` on `engine.connect()`) | partial |
| `backend/ranker/ingest/pipeline.py` | service | batch | `ranker/api/health.py` + `ranker/main.py` (`anyio.to_thread.run_sync`) | partial |
| `backend/ranker/llm/client.py` (`LLMGateway`) | service | request-response | `embedder.py` (Protocol + build_ factory from Settings) | role-match |
| `backend/ranker/llm/providers/anthropic.py` | service (provider) | request-response | `embedder.FastEmbedEmbedder` (lazy heavy import) | role-match |
| `backend/ranker/llm/providers/fake.py` | test double | request-response | `embedder.FakeEmbedder` | exact |
| `backend/ranker/llm/{cost.py, schemas.py, errors.py}` | utility / model | transform | none | no analog |
| `backend/ranker/tasks/{queue.py, consumer.py, handlers.py}` | service | event-driven (queue) | `worker/__main__.py` (stop-event loop with `wait_for` timeout) | partial |
| `backend/ranker/api/admin.py` | controller | request-response | `backend/ranker/api/session.py` + `api/health.py` | role-match |
| `docs/decisions/0001-sources.md` | doc (ADR) | — | none (`docs/` doesn't exist) | no analog |
| `backend/tests/test_migration_0002.py` | test | CRUD | `backend/tests/test_readyz.py` (psycopg direct + `database_url`) | role-match |
| `backend/tests/test_normalize_*.py`, `test_skills.py`, `test_scope.py`, `test_hashing.py` | test (table-driven) | transform | none (no parametrized pure tests yet); `test_worker.py` parametrize style | partial |
| `backend/tests/test_sources_*.py` (respx) | test | streaming | none (respx not used yet) | no analog |
| `backend/tests/test_pipeline.py`, `test_expiry.py` | test (integration) | batch | `backend/tests/test_readyz.py` | role-match |
| `backend/tests/test_llm_gateway.py`, `test_llm_schemas.py` | test | request-response | `backend/tests/test_readyz.py::test_readyz_model_load_failure` (monkeypatch) | partial |
| `backend/tests/test_tasks_queue.py` | test (concurrency) | event-driven | `test_readyz.py` | partial |
| `backend/tests/test_admin_ingest.py` | test (API) | request-response | `backend/tests/test_session_cookie.py` + `conftest.make_client` | exact |
| `backend/tests/test_cli.py` | test | request-response | `backend/tests/test_worker.py` (subprocess) | role-match |
| `backend/tests/test_ingest_redaction.py` | test | — | `backend/tests/test_logging_redaction.py` | exact |
| `backend/tests/test_env_example.py` (unchanged; it auto-enforces new settings) | test | — | itself | exact |

---

## Pattern Assignments

### `backend/migrations/versions/0002_jobs_index.py` (migration)

**Analog:** `backend/migrations/versions/0001_baseline_vector.py` (whole file, 20 lines)

```python
"""Baseline: create the pgvector extension.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")
```

Apply:
- Hand-numbered revision IDs (`revision = "0002"`, `down_revision = "0001"`), not alembic hashes. Docstring header in the same format.
- Use `op.execute()` for `CREATE EXTENSION IF NOT EXISTS pg_trgm` / `citext`, the HNSW index (`USING hnsw (embedding vector_cosine_ops)`), GIN indexes, partial indexes (`tasks_dedupe_pending`, `tasks_ready`), and any generated `tsvector` (CLAUDE.md + RESEARCH Pattern 3). `op.create_table` is fine for plain tables.
- Tables (RESEARCH Patterns 3/4/5/6/7 + discovery): `companies`, `jobs` (no `CHECK` on `source`; columns incl. `source_scope`, `content_hash`, `normalizer_version`, `embedding vector(384)`, `embedding_model`, `remote_eligibility`, `remote_regions TEXT[]`, `cities TEXT[]`, `salary_*` + `salary_is_estimate` + `salary_fx_as_of DATE`, `skills TEXT[]`, `skills_text TEXT`, `dedupe_key`, `canonical_job_id`, `missed_runs`, `last_seen_run_id`, `expired_by_run_id`, `status`), `ingest_runs` (+ `skipped`, `notes JSONB`, `calls_used`), `llm_calls`, `llm_enrichment_cache`, `tasks`, `source_boards`.
- `downgrade()` drops in reverse order; it does not drop `vector` (0001 owns it).
- Applied in tests by `conftest.database_url` (`command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")`, conftest lines 33-34 of that fixture); no extra wiring needed.

---

### `backend/migrations/env.py` (modify)

**Analog:** itself, lines 22-23 (in the dump: `# Phase 2 adds ORM models and sets this to Base.metadata.` / `target_metadata = None`).

Change to `from ranker.db.models import Base` and `target_metadata = Base.metadata`. Keep the rest unchanged: URL from `get_settings().database_url`, `NullPool`, `asyncio.run(run_async_migrations())`, and no `fileConfig` call (docstring explains why).

---

### `backend/ranker/db/models.py` (model, CRUD) — no analog

Use SQLAlchemy 2.1 declarative typed style (`DeclarativeBase`, `Mapped[...]`, `mapped_column`). `pgvector.sqlalchemy.Vector(384)` for `embedding` (returns `list`, per CLAUDE.md). Module docstring first line, like every other `ranker/` module. Export `Base` via the barrel:

**Barrel analog** `backend/ranker/db/__init__.py`:
```python
"""Database access."""

from ranker.db.engine import create_engine_from_settings

__all__ = ["create_engine_from_settings"]
```

**Engine** (reuse, do not duplicate) `backend/ranker/db/engine.py`:
```python
def create_engine_from_settings(settings: Settings) -> AsyncEngine:
    return create_async_engine(settings.database_url, pool_size=5, pool_pre_ping=True)
```
Worker, CLI and pipeline all build the engine with this factory and `await engine.dispose()` in a `finally` (pattern from `ranker/main.py` lifespan).

---

### `backend/ranker/settings.py` (modify, config)

**Analog:** itself (whole file):
```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str
    session_secret: SecretStr
    cookie_secure: bool = True
    embedder: Literal["fastembed", "fake"] = "fastembed"
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_threads: int = 2
    log_level: str = "INFO"
    worker_heartbeat_seconds: float = 30.0


@lru_cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]
```

Apply:
- Secrets as `SecretStr | None = None` (unset = feature disabled): `adzuna_app_id`, `adzuna_app_key`, `anthropic_api_key`, `admin_token`.
- Mode switches as `Literal` exactly like `embedder`: `llm_provider: Literal["anthropic", "fake"] = "anthropic"`.
- Plain typed defaults: `llm_model_fast: str = "claude-haiku-5-5"`, `llm_model_agent: str = "claude-sonnet-5-5"`, `llm_monthly_budget_usd: float = 5.0`, `config_dir: Path = Path(__file__).resolve().parents[1] / "config"`, task consumer poll/concurrency.
- **Every new field must be added to `.env.example`** or `tests/test_env_example.py::test_every_settings_field_documented` fails (it upper-cases `Settings.model_fields`).
- Tests that change env must call `get_settings.cache_clear()` (see `conftest.make_client`).

---

### `.env.example` (modify)

**Analog:** itself. Sectioned with `# --- Section ---` headers, placeholders only. Add a `# --- Ingestion / LLM / Admin (ranker.settings) ---` section, for example:
```
# Adzuna (developer.adzuna.com). Empty disables the adapter.
ADZUNA_APP_ID=
ADZUNA_APP_KEY=
LLM_PROVIDER=anthropic
# Empty = LLM fallbacks skipped, fields stay unknown (D-09)
ANTHROPIC_API_KEY=
LLM_MODEL_FAST=claude-haiku-5-5
LLM_MODEL_AGENT=claude-sonnet-5-5
LLM_MONTHLY_BUDGET_USD=5
# Empty disables /api/admin/*. Generate with: openssl rand -hex 32
ADMIN_TOKEN=
CONFIG_DIR=/app/config
```
Constraint from `test_no_real_looking_secrets`: no value matching `=\s*[0-9a-fA-F]{32,}`.

---

### `backend/ranker/config_loader.py` (utility, file-I/O)

**Analog:** `backend/ranker/settings.py` — Pydantic model + `@lru_cache` getter. Copy the shape:
```python
@lru_cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]
```
Use one Pydantic model per YAML (`SourcesConfig`, `CitiesConfig`, `FxConfig` with `as_of: date`, `SkillsConfig`, `LlmPrices`, ...), `yaml.safe_load` + `Model.model_validate(...)`, and an `@lru_cache` loader per file keyed on `config_dir`. Bad YAML must raise at startup. Tests clear the caches like `get_settings.cache_clear()`.

---

### `backend/ranker/selfcheck.py` (modify)

**Analog:** itself. Keep the contract the CI step depends on (`.github/workflows/ci.yml` line 128-129: `docker run --rm --network none ranker-api:ci python -m ranker.selfcheck` then `jq -e '.ok == true and .dim == 384'`). It must still run **without** `DATABASE_URL`/`SESSION_SECRET` (docstring), so read `CONFIG_DIR` via `os.environ.get` like `EMBED_MODEL` (selfcheck lines 13-15), not `get_settings()`. Error output pattern:
```python
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}))
        return 1
```
Add `"configs": [...names loaded...]` to the success JSON (Pitfall 3).

---

### `backend/Dockerfile` (modify)

**Analog:** runtime stage lines:
```dockerfile
WORKDIR /app
COPY ranker ./ranker
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
```
Add `COPY config ./config` beside these. Also check `backend/.dockerignore` does not exclude `config/`. If a `ranker` console script is added via `[project.scripts]`, note the builder runs `uv sync --locked --no-install-project`, so the script entrypoint will **not** be installed; use `python -m ranker.cli` in compose/docs (or install the project in the builder; planner decides).

---

### `backend/pyproject.toml` (modify)

**Analog:** itself. Apply:
- Add runtime deps in the same `"name>=x,<y"` style; move `httpx>=0.28,<0.29` from `dev` to `dependencies`.
- `[dependency-groups].dev`: add respx, time-machine, hypothesis.
- `[tool.pytest.ini_options]`: add `markers = ["llm: real LLM calls (opt-in)"]` and `addopts = "-m 'not llm'"` (Pitfall 4; `filterwarnings = ["error", ...]` already there and turns unknown marks into failures).
- `[tool.ruff.lint.per-file-ignores]`: `"tests/**"` already ignores `S101,S105,S106,S603`. Ruff `S` is enabled on `ranker/`, so `text()` SQL with f-strings (S608) and `random` jitter (S311) need care: use bound params, not f-strings.

---

### `backend/ranker/observability.py` (modify, utility)

**Analog:** itself.

**Redaction keys** (lines 22-34 in the file): add `"app_key"`, `"app_id"`, `"x-admin-token"`, `"api_key"`, `"anthropic_api_key"`:
```python
REDACT_KEYS: frozenset[str] = frozenset(
    {
        "resume_text",
        "message",
        "content",
        ...
        "x-api-key",
    }
)
```
Note `"content"` is already redacted: never log a job description under `content`; log counts and ids only.

**Noisy logger levels** — copy this line in `configure_logging`:
```python
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
```
and add the same for `"httpx"` and `"httpcore"` (Pitfall 1: Adzuna `app_key` in query strings). Optionally add a `QUERY_SECRET_RE` scrub in `_scrub` for `app_key=...` in free-text strings, mirroring `EMAIL_RE.sub("[EMAIL]", value)`.

---

### `backend/ranker/ingest/sources/base.py` + `registry.py` (interface, streaming)

**Analog:** `backend/ranker/embeddings/embedder.py` — Protocol + real impl + fake + factory.
```python
class Embedder(Protocol):
    dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...
...
def build_embedder(settings: Settings) -> Embedder:
    if settings.embedder == "fake":
        return FakeEmbedder()
    return FastEmbedEmbedder(settings.embed_model, settings.embed_threads)
```
Apply: `SourceAdapter(Protocol)` with `name: str`, `expiry_policy: Literal["absence","ttl"]`, `fetch(...) -> AsyncIterator[RawJob]`, `result() -> FetchResult` (RESEARCH Pattern 2 code block is authoritative). Registry = `dict[str, Callable[[Settings, SourcesConfig], SourceAdapter]]`, populated by explicit imports in `sources/__init__.py` with `__all__`, matching `embeddings/__init__.py`:
```python
from ranker.embeddings.embedder import Embedder, FakeEmbedder, FastEmbedEmbedder, build_embedder

__all__ = ["Embedder", "FakeEmbedder", "FastEmbedEmbedder", "build_embedder"]
```
`RawJob.source` is `str`, **not** `Literal` (ING-04).

---

### `backend/ranker/ingest/sources/{remotive,adzuna,greenhouse,lever}.py` + `ingest/http.py` — no analog

No outbound HTTP code exists yet. Use RESEARCH: Source Facts (field maps), Pattern 2, "Don't Hand-Roll" tenacity row, Pitfalls 1/5/6/9/11, Adzuna crawl plan. Conventions to carry over from the codebase:
- **Error recording = type name only**, as in `api/health.py`:
  ```python
      except Exception as exc:  # report the type only, never the DSN
          checks.setdefault("db", {"ok": False, "error": type(exc).__name__})
  ```
  For adapters: `f"{type(exc).__name__}:{status_code}"` into `ingest_runs.errors`, never `str(exc)` (contains the URL with `app_key`).
- `async with httpx.AsyncClient(...)` per fetch (unclosed clients raise `ResourceWarning` → error under `filterwarnings=error`).
- Return tz-aware UTC datetimes (Remotive naive ISO → `replace(tzinfo=UTC)`; Lever epoch **ms**).
- Slug regex `^[a-z0-9][a-z0-9-]{0,62}$` and fixed host allow-list for Greenhouse/Lever.

---

### `backend/ranker/ingest/normalize/*.py`, `skills.py`, `hashing.py`, `scope` (pure transforms)

**Partial analogs:**
- Module-level compiled regex constant (`observability.py`): `EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")`. Compile skill/region/scope patterns once from config (cache the compiled matcher with `lru_cache` keyed on config).
- Hashing (`embedder.FakeEmbedder._one`): `hashlib.sha256(f"{counter}:{text}".encode()).digest()`. For `content_hash` use `sha256("\x1f".join(source_fields)).hexdigest()` over **source-level fields only** (RESEARCH Pattern 3: not `description_clean`). `dedupe_key` via sha1 of normalised parts.
- Everything else (region precedence table, salary parser, city aliases, seniority, company suffix stripping, skill boundaries `(?<![A-Za-z0-9_+#.])` / `(?![A-Za-z0-9_+#])`) comes from RESEARCH "Normalisation Detail". Keep each a pure function `(input, config) -> result`, fully typed (pyright strict).

---

### `backend/ranker/ingest/pipeline.py`, `expiry.py`, embedding pass (service, batch)

**Analog for DB access with raw SQL:** `backend/ranker/api/health.py`:
```python
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
...
        async with asyncio.timeout(3), engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            ...
            ver = (
                await conn.execute(
                    text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                )
            ).scalar_one_or_none()
```
Use `engine.begin()` for write transactions; bound params only (`text("... WHERE source = :s").bindparams(...)` or `execute(stmt, {...})`). The upsert with `RETURNING old.id IS NULL ...` (RESEARCH Pattern 3) is a single multi-row `text()` statement per batch of 200-500 rows.

**Analog for CPU work off the event loop:** `ranker/api/health.py` and `ranker/main.py`:
```python
            with anyio.fail_after(5):
                vec = await anyio.to_thread.run_sync(embedder.embed, ["readiness probe"])
```
```python
        embedder = await anyio.to_thread.run_sync(partial(build_embedder, settings))
```
`embed_missing(engine, embedder, model_name, batch=32)` selects `embedding IS NULL`, builds text (title + company + top skills + first 1,500 chars), and calls `await anyio.to_thread.run_sync(embedder.embed, texts)` (Pitfall 7). Embedder built via `build_embedder(settings)`, so `EMBEDDER=fake` in tests gives deterministic 384-d vectors with no model load.

**Logging:** `structlog.get_logger("ranker.ingest")` with event-name-first, keyword fields, error type only — as in `main.py`:
```python
log = structlog.get_logger("ranker.main")
...
        log.error("embedder_load_failed", error_type=type(exc).__name__)
```
Run lifecycle, advisory lock, volume guard and expiry SQL: RESEARCH Patterns 4 and 5 (no codebase analog).

---

### `backend/ranker/llm/client.py`, `providers/anthropic.py`, `providers/fake.py`, `cost.py`, `schemas.py`, `errors.py` (service, request-response)

**Analog:** `backend/ranker/embeddings/embedder.py` (Protocol + real + fake + `build_*` from Settings).

- `Provider(Protocol)` mirrors `Embedder(Protocol)`.
- Anthropic provider mirrors `FastEmbedEmbedder`'s **lazy heavy import** inside `__init__`:
  ```python
      def __init__(self, model_name: str, threads: int) -> None:
          from fastembed import TextEmbedding  # lazy: keeps onnxruntime out of fake-embedder runs

          self._model: Any = TextEmbedding(model_name=model_name, threads=threads)
  ```
  Do the same for `anthropic.AsyncAnthropic` so fake-provider tests never import/construct the SDK client.
- `FakeProvider` mirrors `FakeEmbedder`: deterministic, network-free, constructor-configurable; additionally records calls and returns synthetic usage (RESEARCH Pattern 6).
- `build_provider(settings)` mirrors `build_embedder`: `if settings.llm_provider == "fake": return FakeProvider(...)`. With `anthropic` and empty `anthropic_api_key`, `available()` is False and calls raise `LLMUnavailable` (D-09).
- Secrets via `settings.anthropic_api_key.get_secret_value()` as `session.py` does with `settings.session_secret.get_secret_value()`.
- Budget/cost/`llm_calls` insert/schemas: RESEARCH Pattern 6 (no analog). No `temperature`; no `dict` fields; errors stored as class names only.

---

### `backend/ranker/tasks/{queue,consumer,handlers}.py` (service, event-driven)

**Analog:** `backend/ranker/worker/__main__.py` stop-event loop:
```python
    while not stop.is_set():
        log.info("worker_heartbeat")
        try:
            await asyncio.wait_for(stop.wait(), timeout=heartbeat)
        except TimeoutError:
            pass
```
Consumer: `consume_tasks(engine, stop, handlers)` loops the same way — claim (SKIP LOCKED SQL from RESEARCH Pattern 7, committed immediately), dispatch by `kind` to a `dict[str, Handler]`, and when the claim is empty wait `asyncio.wait_for(stop.wait(), timeout=poll_seconds)`. Defer-without-attempts on `LLMUnavailable`/`BudgetExceeded` (Pitfall 2). `enqueue()` uses `INSERT ... ON CONFLICT (dedupe_key) WHERE status IN ('queued','running') DO NOTHING RETURNING id`.

---

### `backend/ranker/worker/__main__.py` (modify, event-driven)

**Analog:** itself (whole file). Keep:
```python
async def run(stop: asyncio.Event | None = None) -> None:
    log = structlog.get_logger("ranker.worker")
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)  # PID 1 ignores SIGTERM without a handler
    ...
    log.info("worker_started")
    ...
    log.info("worker_stopped")


def main() -> None:
    configure_logging(get_settings().log_level)
    asyncio.run(run())
```
The placeholder comment (`# Phase 2: start the APScheduler AsyncIOScheduler (apscheduler<4) and the tasks consumer here`) marks the insertion point. Add: engine from `create_engine_from_settings`, leader advisory lock on a held connection, `AsyncIOScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600})`, consumer task, abandoned-run reaper at start (RESEARCH Pattern 8). **Keep the `worker_started` / `worker_heartbeat` / `worker_stopped` log events and exit code 0** — `tests/test_worker.py` asserts them, and runs the worker with an unreachable `DATABASE_URL` (`postgresql+psycopg://x:y@127.0.0.1:1/none`). The worker must therefore keep heartbeating and shut down cleanly when the DB is unreachable (log `db_unavailable` with error type and retry), or the test must be updated deliberately. Never import apscheduler from `ranker.main` / `ranker.api` (OPS-02 import test).

---

### `backend/ranker/api/admin.py` (controller, request-response)

**Analog:** `backend/ranker/api/session.py` (settings from `request.app.state`, secret handling) + `api/health.py` (engine from `request.app.state.engine`).
```python
router = APIRouter()
...
@router.get("/session/ping")
def session_ping(request: Request, response: Response) -> dict[str, bool]:
    settings: Settings = request.app.state.settings
    s = URLSafeTimedSerializer(settings.session_secret.get_secret_value(), salt="rk-ping-v1")
```
```python
    engine: AsyncEngine = request.app.state.engine
```
Apply: `router = APIRouter(prefix="/admin")`; a swappable dependency `require_admin(request, x_admin_token: Annotated[str | None, Header()] = None)` that raises 404 when `settings.admin_token is None`, 401 on `not hmac.compare_digest(...)`; `POST /ingest/{source}` validates against the source registry (404), enqueues via `tasks.queue.enqueue` and returns 202 `{"task_id": ...}` or 409. Return types annotated (pyright strict). No fetching in the API.

**Registration analog** `backend/ranker/api/__init__.py`:
```python
from ranker.api import health, session

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(session.router)
```
Add `admin` to the import and `api_router.include_router(admin.router)`. `main.create_app` mounts `api_router` under `/api`, so the final path is `/api/admin/ingest/{source}`.

---

### `backend/ranker/cli.py` (controller, CLI)

**Analogs:** `backend/ranker/selfcheck.py` (`main() -> int`, JSON line output, exit code via `sys.exit(main())`) and `worker/__main__.py` (`configure_logging(get_settings().log_level)` then `asyncio.run(...)`).

Apply: `app = typer.Typer()`; commands `ingest <source>`, `embed --missing/--all [--model]`, `spike sources`, `boards report`, `purge-source <name>`, `tasks ...`. Each command calls `configure_logging(...)` first (so httpx loggers are silenced), then `asyncio.run(_impl(...))`, and maps outcomes to exit codes (`raise typer.Exit(code=2)` when the per-source advisory lock is held). Add `if __name__ == "__main__": app()` so `python -m ranker.cli` works in the image (see Dockerfile note).

---

### `deploy/compose.prod.yaml` (modify)

**Analog:** the existing `worker` service:
```yaml
  worker:
    image: ghcr.io/${GHCR_OWNER}/ranker-api:${IMAGE_TAG:-main}
    command: ["python", "-m", "ranker.worker"]
    env_file: .env
    init: true
    restart: unless-stopped
    stop_grace_period: 10s
    depends_on:
      db:
        condition: service_healthy
```
Add `mem_limit` (e.g. `1g`) to `worker` and `api` (Pitfall 8). No `replicas`/scale (single scheduler; the advisory lock is the safety net). Never add `ports:` (file header comment). Mirror any change in root `compose.yaml` (it also defines `worker`, line 43-45).

---

## Test Patterns

### Fixtures — `backend/tests/conftest.py`
Env defaults are set **before** any `ranker` import:
```python
os.environ.setdefault("SESSION_SECRET", "test-secret-" + "x" * 52)
os.environ.setdefault("EMBEDDER", "fake")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("COOKIE_SECURE", "true")
```
Add `os.environ.setdefault("LLM_PROVIDER", "fake")` here. Session-scoped container + migration:
```python
@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    with PostgresContainer(PG_IMAGE, driver="psycopg") as pg:
        yield pg


@pytest.fixture(scope="session")
def database_url(pg_container: PostgresContainer) -> Iterator[str]:
    url = pg_container.get_connection_url()
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    # Sync fixture, so asyncio.run() inside migrations/env.py is legal.
    command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")
    yield url
```
New fixtures to add: an async `engine` fixture built from `database_url` (dispose on teardown) and a per-test cleanup (`TRUNCATE jobs, companies, ingest_runs, tasks, llm_calls, ... RESTART IDENTITY CASCADE`) since tests share one DB. `asyncio_default_fixture_loop_scope = "session"` is set in pyproject.

### API tests — `backend/tests/test_session_cookie.py` + `conftest.make_client`
```python
def _make(env_overrides: dict[str, str]) -> TestClient:
    for key, value in env_overrides.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    c = TestClient(create_app(), base_url="https://testserver")
```
`test_admin_ingest.py`: `make_client({"DATABASE_URL": database_url})` with no token → 404; `make_client({..., "ADMIN_TOKEN": "t"*40})` → 401 with a bad header, 202 with the right one, then assert a `tasks` row exists.

### Monkeypatch failure injection — `backend/tests/test_readyz.py`
```python
    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("model download failed")

    monkeypatch.setattr("ranker.main.build_embedder", boom)
```
And secret-not-leaked assertion style:
```python
    assert secret not in r.text
```
Use both for Adzuna key leakage (respx 500 → assert `app_key` value absent from logs and `ingest_runs.errors`).

### Direct psycopg checks — `backend/tests/test_readyz.py`
```python
    url = make_url(database_url)
    plain = f"postgresql://{url.username}:{url.password}@{url.host}:{url.port}/postgres"
    with psycopg.connect(plain, autocommit=True) as conn:
```
Useful for migration tests (inspect `pg_indexes` for the HNSW index) and the two-consumer SKIP LOCKED test.

### Rendered-log assertions — `backend/tests/test_logging_redaction.py`
```python
@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    yield
    structlog.reset_defaults()
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def _configure() -> io.StringIO:
    from ranker.observability import configure_logging

    buf = io.StringIO()
    configure_logging(level="DEBUG", stream=buf)
    return buf
```
Assert on `buf.getvalue()` (rendered JSON), never `capture_logs`. Copy for `test_ingest_redaction.py` (httpx logger silenced; `app_key` scrubbed).

### Subprocess process tests — `backend/tests/test_worker.py`
`@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])`, `subprocess.Popen([sys.executable, "-m", "ranker.worker"], cwd=BACKEND_DIR, env=env, ...)`, read lines until an event appears, send signal, `communicate(timeout=5)`, assert `returncode == 0`. Copy for `test_cli.py` exit codes (`[sys.executable, "-m", "ranker.cli", "ingest", "nope"]`) and for a "second worker does not take the scheduler lock" test (log event `scheduler_lock_held_elsewhere`).

---

## Shared Patterns

### Settings access
**Source:** `backend/ranker/settings.py` (`get_settings()`, `lru_cache`); API code reads `request.app.state.settings` (`api/session.py`). **Apply to:** all new modules. Non-API code calls `get_settings()` once at entry (worker/CLI) and passes `Settings` down explicitly (like `create_engine_from_settings(settings)` / `build_embedder(settings)`), which keeps pure modules testable.

### Error reporting = exception type only
**Source:** `backend/ranker/api/health.py` (`"error": type(exc).__name__`), `ranker/main.py` (`log.error("embedder_load_failed", error_type=type(exc).__name__)`), `selfcheck.py`. **Apply to:** adapters, `ingest_runs.errors`, `llm_calls.error`, `tasks.last_error`, admin endpoint. Never `str(exc)` (URLs/keys/DSNs).

### Structured logging
**Source:** `backend/ranker/observability.py` + `structlog.get_logger("ranker.<area>")` with snake_case event names (`worker_started`, `embedder_load_failed`). **Apply to:** all new modules. Logger names: `ranker.ingest`, `ranker.llm`, `ranker.tasks`, `ranker.worker`, `ranker.cli`, `ranker.admin`. Never log job descriptions or prompts; `content`/`message` keys are masked anyway.

### Real + fake implementation behind a Protocol, chosen by a Settings `Literal`
**Source:** `backend/ranker/embeddings/embedder.py` (`Embedder`, `FastEmbedEmbedder`, `FakeEmbedder`, `build_embedder`). **Apply to:** LLM provider (`LLM_PROVIDER=fake|anthropic`), source adapters (fake adapter for pipeline tests), and any clock/HTTP seams.

### Blocking/CPU work off the loop
**Source:** `ranker/api/health.py` / `ranker/main.py` (`await anyio.to_thread.run_sync(...)`, `partial(...)` for kwargs). **Apply to:** embedding batches, fastembed model load in the worker, any large regex pass over many descriptions.

### Raw SQL via `text()` on the async engine
**Source:** `ranker/api/health.py`. **Apply to:** upsert with `RETURNING old/new`, SKIP LOCKED claim, advisory locks, expiry, budget sum. Bound params only (ruff `S608`).

### Module docstrings and typing
Every `ranker/` module starts with a one-line docstring (e.g. `"""Async SQLAlchemy engine factory."""`), uses `from collections.abc import ...`, PEP 604 unions, and full annotations (pyright strict on `ranker/`). `Any` only at third-party boundaries with a comment (`self._model: Any`).

### Env documentation guard
**Source:** `backend/tests/test_env_example.py`. **Apply to:** every new Settings field and every new deploy/CI variable (add server-only names to its `EXTRA` list if they are not Settings fields).

---

## No Analog Found

Planner should use RESEARCH.md patterns for these:

| File | Role | Data Flow | Reason / RESEARCH section |
|------|------|-----------|---------------------------|
| `backend/ranker/db/models.py` | model | CRUD | No ORM models yet. SPEC §8 + RESEARCH Patterns 3-7 |
| `backend/config/*.yaml` | config data | file-I/O | No config files yet. RESEARCH Pattern 1, Adzuna crawl plan, Normalisation Detail |
| `backend/ranker/ingest/sources/{remotive,adzuna,greenhouse,lever}.py` | adapter | streaming HTTP | No outbound HTTP code. RESEARCH Source Facts, Pattern 2 |
| `backend/ranker/ingest/http.py` | utility | request-response | tenacity policy + `redact_url`. RESEARCH Don't Hand-Roll, Pitfall 1 |
| `backend/ranker/ingest/normalize/*.py`, `skills.py` | pure transform | transform | Domain rules. RESEARCH Normalisation Detail |
| `backend/ranker/ingest/expiry.py` | service | batch | RESEARCH Pattern 5 |
| `backend/ranker/llm/{cost,schemas,errors}.py` | utility/model | transform | RESEARCH Pattern 6 (schema-lint test walks `model_json_schema()`) |
| `backend/ranker/tasks/queue.py` SQL | service | queue | RESEARCH Pattern 7 (claim query verified on PG18) |
| `docs/decisions/0001-sources.md` | ADR | — | `docs/` doesn't exist. RESEARCH ING-00 Spike Design step 4 lists required contents |
| `backend/tests/test_sources_*.py` (respx) | test | streaming | respx not used yet; fixtures from RESEARCH Source Facts payload shapes |

## Metadata

**Analog search scope:** `backend/ranker/**`, `backend/tests/**`, `backend/migrations/**`, `backend/Dockerfile`, `backend/pyproject.toml`, `backend/alembic.ini`, `deploy/compose.prod.yaml`, `compose.yaml`, `.env.example`, `.github/workflows/ci.yml`
**Files scanned:** 30 (every tracked backend Python file, plus config/deploy files)
**Pattern extraction date:** 2026-11-21
