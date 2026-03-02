# GA4GH Data Connect

The backend includes an optional [GA4GH Data Connect v1.2.0](https://github.com/ga4gh-discovery/data-connect) implementation that exposes datasets via a standardised API.

## Enabling

Set `ENABLE_DATACONNECT` in your `.env` file:

```
ENABLE_DATACONNECT=true
```

Or export it to the shell before starting the server:

```bash
export ENABLE_DATACONNECT=true
```

When disabled (the default), the `/dataconnect` endpoints are not registered and return 404.

## Endpoints

When enabled, the following endpoints are available under `/dataconnect`:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/dataconnect/service-info` | GET | GA4GH service metadata |
| `/dataconnect/tables` | GET | List all available tables (from `view.source`) |
| `/dataconnect/table/{name}/info` | GET | JSON Schema for a table, enriched with `view_column` metadata |
| `/dataconnect/table/{name}/data` | GET | Paginated data with `page` and `page_size` parameters |
| `/dataconnect/query` | POST | Execute arbitrary SELECT queries with optional parameters |

## How it works

- **Table listing** queries `view.source` for available tables. URLs in the response are built from the incoming request so they work behind reverse proxies.
- **Table info** builds JSON Schema properties by joining `view_column` metadata (labels, URL templates, delimiters) with DuckDB native types. Columns not in `view_column` fall back to DuckDB type mapping only.
- **All columns are exposed**, including hidden ones — Data Connect allows raw SQL execution so hiding columns at this layer would be inconsistent.
- **Query endpoint** uses sqlglot to validate that only SELECT statements are accepted. It also supports the `ga4gh_type(value, schema_ref)` macro for annotating columns with `$ref` schema references.

## Dependencies

- `sqlglot` — SQL parsing and validation (added to `requirements.txt`)

## Files

- `backend/dataconnect/dataconnect.py` — Router with all endpoints and helpers
- `backend/models/dataconnect.py` — `DataConnectQuery` Pydantic model
- `backend/tests/test_dataconnect.py` — 17 tests covering all endpoints, URL correctness, error handling, and optional config
