# Backend Migration to data-loader-etl v2 Schema

## Why

The amr-portal backend previously relied on an in-tree ETL pipeline (`scripts/etl/`) that produced DuckDB databases with a schema built around `dataset`, `column_definition`, `category`, `filter`, and `category_group` tables. This schema had several limitations:

- Column metadata was spread across multiple junction tables (`column_definition`, `dataset_column`, `view_column`), making queries verbose and fragile.
- Filters only supported simple categorical value lists — no range queries, prefix matching, genomic location overlap, or array membership.
- Filter grouping used a `category_group` table with an `is_primary` boolean, splitting groups into two fixed buckets (`categoryGroups` / `otherCategoryGroups`) rather than allowing flexible ranked ordering.
- The ETL code was tightly coupled to the AMR portal and not reusable for other data portals.

## Schema Changes

| Concept | Old (v1) | New (v2) |
|---------|----------|----------|
| Dataset resolution | `dataset` + `dataset_column` tables; `view_categories.dataset_name` | `view.source` column directly |
| Column metadata | Separate `column_definition` table with `fullname` = `dataset-col` | Inline in `view_column` — no join needed |
| Column ↔ view link | `view_column` → `column_definition` via `column_id` FK | `view_column` has all metadata directly |
| Filters | `category` + `filter` tables; values are always simple lists | `view_filter` + `view_filter_value`; supports 6 filter types |
| Filter grouping | `category_group` + `category_group_category` with `is_primary` flag | `view_filter_group` with `rank` — single ordered list |
| Filter identity | Integer `column_id` via `column_definition` | String `view_filter.id` (globally unique) |
| Convenience views | `view_categories` (one large join) | `filter_config` + `column_config` (two focused views) |
| Release metadata | `release_label` only | `release_label` + `schema_version` |

### Filter Types

The v2 schema supports six filter types, up from one:

| Type | SQL Pattern | Use Case |
|------|-------------|----------|
| `select_list` | `WHERE col IN (...)` | Pre-computed value lists (same as v1) |
| `select` | `WHERE col = ?` or `WHERE col LIKE ?%` | Single value, exact or prefix match |
| `select_in` | `WHERE col IN (...)` | Multiple exact values |
| `range` | `WHERE col BETWEEN ? AND ?` | Numeric min/max range |
| `location` | Overlap query on region/start/end/strand | Genomic coordinate queries |
| `list_contains` | `WHERE list_contains(col, ?)` | Array/list membership |

### Unique Filter IDs

The `view_filter` table has a `UNIQUE("id")` constraint, meaning filter IDs must be globally unique across all views. Since the same logical filter (e.g. "Antibiotic") appears in multiple views backed by different datasets, each view's filters use a prefixed ID:

- `pheno_antibiotic_name` (phenotype view)
- `geno_antibiotic_name` (genotype view)
- `combined_antibiotic_name` (combined view)

Each filter specifies `query_columns: { column: antibiotic_name }` to map back to the actual data column.

## Files Changed

### `backend/core/filters_config_parser.py` — Major rewrite

All SQL queries were rewritten to use the v2 schema tables and convenience views:

- `_build_filter_categories()` now queries `filter_config` joined with `view_filter_value`, resolves `query_columns` to construct `source-column` prefixed category IDs, and populates filter type metadata.
- `_build_filter_views()` now queries `filter_config` directly. Uses `view_filter_group.rank` for ordering instead of the `is_primary` boolean. Outputs a single `filterGroups` list.
- `_build_columns_per_view()` queries `view_column` directly (metadata is inline) instead of joining through `column_definition`.
- New `_resolve_column_name()` helper maps unique filter IDs back to actual column names via `query_columns`.

### `backend/services/filters.py` — Moderate rewrite

- `get_dataset_from_view()` queries `view.source` instead of `view_categories.dataset_name`.
- `get_display_column_details()` queries `view_column` directly, constructing the `source-column` prefixed `fullname` in SQL.
- New `get_filter_metadata()` loads filter type info per view for type-aware query generation.
- New `_build_where_clause()` generates SQL appropriate to each filter type.
- New `_resolve_query_column()` resolves the actual data column from `query_columns`.
- `_build_filter_query_context()` uses `get_filter_metadata()` for validation and type-aware WHERE clause generation instead of simple `IN (...)` for all filters.

### `backend/models/filters_config.py` — Updated models

- `FilterCategory` gains `filter_type`, `match_type`, `min`, `max`, `query_columns`, `regex` fields.
- New `FilterGroup` model replaces `CategoryGroup`, adding `id` and `rank`.
- `FilterView` uses a single `filterGroups: list[FilterGroup]` instead of `categoryGroups` + `otherCategoryGroups`.
- `Column` gains a `hidden` field.

### `backend/models/release.py` — Minor addition

- Added optional `schema_version` field.

### `backend/services/release.py` — Minor query update

- Release query now fetches `schema_version` alongside `release_label`.

### `backend/core/utils.py` — NaN sanitization

- Added `_sanitize_nan()` helper to convert `NaN` float values to `None` after `query_to_records()`. DuckDB NULL values in float columns become `NaN` when passed through pandas `fetchdf().to_dict()`, which causes `ValueError: Out of range float values are not JSON compliant: nan` in FastAPI's strict JSON serializer. This affects the `min` and `max` fields on `select_list` filters (which are NULL when not applicable).

### Unchanged files

- `backend/services/serializer.py` — Compatible as-is; still keyed by prefixed `fullname`.
- `backend/models/payload.py` — The `source-column` prefix convention is preserved.
- `backend/api/endpoints.py` — No changes needed.
- `backend/core/database.py` — Connection management is schema-independent.
- `backend/core/config.py` — DuckDB path configuration is schema-independent.

## Frontend Impact

The API response structure has one breaking change:

**`/api/filters-config` response**: `FilterView` objects now contain `filterGroups` (a single ranked list) instead of `categoryGroups` + `otherCategoryGroups`. The frontend needs to read from `filterGroups` and use `rank` for ordering.

All other API contracts are preserved:
- Filter category IDs use the same `source-column` format (e.g. `phenotype-antibiotic_name`).
- Column IDs in data records use the same `source-column` prefix.
- `/api/amr-records` request/response format is unchanged.
- `/api/amr-records/download` streaming format is unchanged.

## ETL Configuration

New ETL configuration files were created at `data-loader-etl/amr/`:

- `config.yaml` — Filter definitions (21 unique per-view filters), 3 views with filter groups and column overrides.
- `data.yaml` — Dataset definitions pointing to source CSV files with computed column SQL expressions.
- `data.local.yaml` — Local test variant pointing to existing parquet files in `amr-portal/scripts/etl/alpha_v3/`.

To generate a new database:

```bash
cd data-loader-etl
uv run python main.py -r amr_release -c amr/config.yaml -d amr/data.yaml -f
```

Then point the backend at the generated DuckDB:

```bash
DUCKDB_PATH=data-loader-etl/amr_release/amr_release.duckdb
```
