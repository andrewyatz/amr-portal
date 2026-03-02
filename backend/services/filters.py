import csv
import io
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from itertools import chain
from typing import Any, Dict, Iterable, Iterator, Optional

import duckdb
import numpy as np
from functools import lru_cache
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
import logging

from backend.models.payload import Payload
from backend.services.serializer import serialize_amr_record
from backend.core.filters_config_parser import build_filters_config
from backend.core.config import get_settings

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
_settings = get_settings()

# Cache display columns per view_id so repeated downloads do not incur extra metadata queries.
_display_columns_cache: Dict[int, Any] = {}

# Cache filter metadata per view for building type-aware queries.
_filter_meta_cache: Dict[int, Dict[str, Dict[str, Any]]] = {}


@dataclass
class FilterQueryContext:
    """Container for all SQL/query metadata needed to stream AMR records."""
    dataset: str
    base_query: str
    count_query: str
    params: list
    display_column_details: dict
    order_by_col: Optional[str]


@lru_cache(maxsize=32)
def get_table_columns(table_name: str, db: duckdb.DuckDBPyConnection):
    """Return the set of column names for the provided table.

    Args:
        table_name (str): Target DuckDB table name.
        db (duckdb.DuckDBPyConnection): Database connection to query through.

    Returns:
        set[str]: Column names available on the table.

    Raises:
        HTTPException: If DuckDB fails to return table metadata.
    """    
    try:
        columns_result = db.query(f"PRAGMA table_info({table_name})").fetchdf()
        return set(columns_result['name'].tolist())
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to get columns for table: {table_name}")


def get_dataset_from_view(view_id: int, db: duckdb.DuckDBPyConnection):
    """Resolve the dataset (source table) backing a view_id.

    Args:
        view_id (int): View identifier coming from the UI configuration.
        db (duckdb.DuckDBPyConnection): Database connection.

    Returns:
        str: Dataset/table name associated with the view.

    Raises:
        HTTPException: If the lookup fails.
    """
    query = f"SELECT source FROM view WHERE view_id = {view_id};"
    try:
        dataset = db.execute(query).fetchone()[0]
        return dataset
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to get dataset from view ID: {view_id}")


def get_display_column_details(view_id: int, db: duckdb.DuckDBPyConnection):
    """Return per-column metadata for a view, caching results for reuse.

    Queries view_column directly (new schema has metadata inline,
    no join to column_definition needed). Constructs prefixed fullname
    for frontend compatibility.
    """
    if view_id in _display_columns_cache:
        return _display_columns_cache[view_id].copy()

    columns_to_display_query = f"""
        SELECT
            CONCAT(v.source, '-', vc.name) AS fullname,
            vc.name,
            vc.type,
            vc.sortable,
            vc.url,
            vc.delimiter
        FROM view AS v
            JOIN view_column vc ON v.view_id = vc.view_id
        WHERE v.view_id = {view_id}
            AND vc.hidden = false
        ORDER BY vc.rank;
    """
    try:
        columns = db.execute(columns_to_display_query).fetchdf()
        _display_columns_cache[view_id] = columns
        return columns.copy()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to get columns to display from view ID: {view_id}")


def get_filter_metadata(view_id: int, db: duckdb.DuckDBPyConnection) -> Dict[str, Dict[str, Any]]:
    """Load filter type metadata for a view, keyed by prefixed filter id.

    Used to build type-appropriate WHERE clauses.
    """
    if view_id in _filter_meta_cache:
        return _filter_meta_cache[view_id]

    query = f"""
        SELECT
            fc.source,
            fc.filter_name,
            fc.filter_type,
            fc.match_type,
            fc.query_columns,
            fc.regex
        FROM filter_config fc
        WHERE fc.view_dbid = {view_id}
    """
    try:
        rows = db.execute(query).fetchdf().to_dict(orient="records")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to get filter metadata for view ID: {view_id}")

    meta = {}
    for r in rows:
        query_columns = r.get("query_columns")
        if query_columns and isinstance(query_columns, str):
            query_columns = json.loads(query_columns)
        # Resolve the actual column name for the prefixed key (frontend compatibility)
        col_name = r["filter_name"]
        if query_columns and isinstance(query_columns, dict) and "column" in query_columns:
            col_name = query_columns["column"]
        prefixed_id = f"{r['source']}-{col_name}"
        meta[prefixed_id] = {
            "filter_name": col_name,
            "filter_type": r["filter_type"],
            "match_type": r.get("match_type"),
            "query_columns": query_columns,
            "regex": r.get("regex"),
        }
    _filter_meta_cache[view_id] = meta
    return meta


def quote_column_name(column_name):
    """Quote a column name for DuckDB SQL usage.

    Args:
        column_name (str): Unquoted identifier.

    Returns:
        str: Identifier quoted with double quotes.
    """
    return f'"{column_name}"'


def fetch_filters(db):
    """Load the filters configuration used by the UI.

    Args:
        db (duckdb.DuckDBPyConnection): Database connection.

    Returns:
        dict: Parsed filters configuration.
    """
    return build_filters_config(db)


def _normalize_value(value):
    """Normalize scalar values so NaN/inf do not leak into serialized output.

    Args:
        value: Arbitrary scalar fetched from DuckDB.

    Returns:
        Any: Value safe to serialize.
    """
    if value is None:
        return None
    if isinstance(value, (float, np.floating)):
        if math.isnan(value) or math.isinf(value):
            return None
    return value


def _append_order_clause(query: str, payload: Payload, order_by_col: Optional[str]) -> str:
    """Add an ORDER BY clause if the payload specifies a sortable column.

    Args:
        query (str): Base SQL query.
        payload (Payload): Request payload that may include order_by.
        order_by_col (Optional[str]): Sanitized column name or None.

    Returns:
        str: Query with ORDER BY appended when required.
    """
    if payload.order_by and order_by_col:
        return f"{query} ORDER BY {quote_column_name(order_by_col)} {payload.order_by.order}"
    return query


def _resolve_query_column(filter_meta: Dict[str, Any]) -> str:
    """Determine the actual column to query for a filter.

    If query_columns is set, use the specified column; otherwise default
    to the filter_name (which is the column name by convention).
    """
    qc = filter_meta.get("query_columns")
    if qc and isinstance(qc, dict):
        if "column" in qc:
            return qc["column"]
    return filter_meta["filter_name"]


def _build_where_clause(
    category: str,
    values: list[str],
    filter_meta: Dict[str, Dict[str, Any]],
) -> Optional[tuple[str, list]]:
    """Build a type-appropriate WHERE clause fragment for a single filter.

    Args:
        category: The prefixed filter category id (e.g. "phenotype-antibiotic_name").
        values: The user-selected values for this filter.
        filter_meta: Filter metadata dict keyed by prefixed filter id.

    Returns:
        Tuple of (SQL WHERE clause fragment with ? placeholders, list of bind params),
        or None if the filter cannot be applied.
    """
    meta = filter_meta.get(category)
    if not meta:
        col = category.split("-", 1)[-1]
        placeholders = ", ".join("?" for _ in values)
        return f"{quote_column_name(col)} IN ({placeholders})", list(values)

    filter_type = meta["filter_type"]
    col = _resolve_query_column(meta)
    match_type = meta.get("match_type", "exact")

    if filter_type in ("select_list", "select_in"):
        placeholders = ", ".join("?" for _ in values)
        return f"{quote_column_name(col)} IN ({placeholders})", list(values)

    elif filter_type == "select":
        if match_type == "prefix":
            clauses = [f"{quote_column_name(col)} LIKE ?" for _ in values]
            params = [f"{v}%" for v in values]
            return f"({' OR '.join(clauses)})", params
        else:
            if len(values) == 1:
                return f"{quote_column_name(col)} = ?", [values[0]]
            placeholders = ", ".join("?" for _ in values)
            return f"{quote_column_name(col)} IN ({placeholders})", list(values)

    elif filter_type == "range":
        try:
            numeric_values = [float(v) for v in values]
        except (ValueError, TypeError):
            return None
        if len(numeric_values) >= 2:
            return f"{quote_column_name(col)} BETWEEN ? AND ?", [numeric_values[0], numeric_values[1]]
        elif len(numeric_values) == 1:
            return f"{quote_column_name(col)} = ?", [numeric_values[0]]
        return None

    elif filter_type == "list_contains":
        clauses = [f"list_contains({quote_column_name(col)}, ?)" for _ in values]
        return f"({' OR '.join(clauses)})", list(values)

    elif filter_type == "location":
        qc = meta.get("query_columns", {})
        regex_pattern = meta.get("regex")
        if not qc or not regex_pattern:
            return None

        location_str = values[0] if values else None
        if not location_str:
            return None

        match = re.match(regex_pattern, location_str)
        if not match:
            return None

        groups = match.groupdict()
        region_col = qc.get("region", "region")
        start_col = qc.get("start", "region_start")
        end_col = qc.get("end", "region_end")

        region = groups.get("region")
        start_str = groups.get("start")
        end_str = groups.get("end")
        strand = groups.get("strand")

        clauses = [f"{quote_column_name(region_col)} = ?"]
        params: list = [region]

        if start_str and end_str:
            try:
                start_val = int(start_str)
                end_val = int(end_str)
            except ValueError:
                return None
            clauses.append(f"{quote_column_name(start_col)} <= ?")
            clauses.append(f"{quote_column_name(end_col)} >= ?")
            params.extend([end_val, start_val])

        if strand and "strand" in qc:
            strand_col = qc["strand"]
            clauses.append(f"{quote_column_name(strand_col)} = ?")
            params.append(strand)

        return f"({' AND '.join(clauses)})", params

    # Unknown filter type - fallback to IN
    placeholders = ", ".join("?" for _ in values)
    return f"{quote_column_name(col)} IN ({placeholders})", list(values)


def _build_filter_query_context(payload: Payload, db: duckdb.DuckDBPyConnection) -> FilterQueryContext:
    """Pre-compute shared SQL fragments/metadata for both paged and streaming exports.

    Updated for the new v2 schema: uses view.source for dataset resolution,
    view_column for metadata, and filter_config for type-aware query building.
    Args:
        payload (Payload): Request payload containing selected filters and sorting.
        db (duckdb.DuckDBPyConnection): Database connection.

    Returns:
        FilterQueryContext: Object encapsulating dataset info and reusable SQL snippets.

    Raises:
        HTTPException: If the view_id is missing, filters are invalid, or order_by references an unknown column.
    """
    selected_view_id = payload.view_id
    # Check if the view_id is specified
    if not selected_view_id:
        raise HTTPException(
            status_code=400,
            detail="Please specify a view ID to filter by."
        )

    # Now we use the selected view to infer which dataset to query data from
    selected_dataset = get_dataset_from_view(selected_view_id, db)

    valid_columns = get_table_columns(selected_dataset, db)
    # not all valid columns are eventually displayed
    # We need to keep only the ones we are interested
    columns_to_display = get_display_column_details(selected_view_id, db)

    # This will be used below in the SQL query to select only columns we are interested in
    # Properly quote column names for SQL query
    quoted_columns = [quote_column_name(col) for col in columns_to_display["name"]]
    columns_to_display_str = ", ".join(quoted_columns)
    
    # Build dict of column details for serializer
    columns_to_display_dict = columns_to_display.to_dict('records')
    display_column_details = {r["fullname"]: r for r in columns_to_display_dict}

    # Load filter type metadata for this view
    filter_meta = get_filter_metadata(selected_view_id, db)

    # Group selected filters by category (prefixed id)
    grouped_filters = defaultdict(list)
    for f in payload.selected_filters:
        grouped_filters[f.category].append(f.value)

    # Validate: for each filter category, check the resolved column exists
    for category in grouped_filters:
        meta = filter_meta.get(category)
        if meta:
            col = _resolve_query_column(meta)
            if col not in valid_columns:
                raise HTTPException(
                    status_code=400,
                    detail=f"Filter column '{col}' does not exist in dataset '{selected_dataset}'."
                )
        else:
            col = category.split("-", 1)[-1]
            if col not in valid_columns:
                raise HTTPException(
                    status_code=400,
                    detail=f"Filter column '{col}' does not exist in dataset '{selected_dataset}'."
                )

    logger.info(f"selected_view_id: {selected_view_id}")
    logger.info(f"selected_dataset: {selected_dataset}")
    logger.info(f"grouped_filters: {grouped_filters}")
    logger.info(f"quoted_columns: {quoted_columns}")

    order_by_col = None
    if payload.order_by:
        order_by_col = payload.order_by.category.split("-")[-1]
        if order_by_col not in valid_columns:
            raise HTTPException(status_code=400, detail=f"Invalid order_by column: {order_by_col!r}")

    # Build type-aware WHERE clauses with bind parameters
    where_clauses = []
    all_params = []
    for category, values in grouped_filters.items():
        result = _build_where_clause(category, values, filter_meta)
        if result:
            clause, params = result
            where_clauses.append(clause)
            all_params.extend(params)

    where_sql = " AND ".join(where_clauses)
    base_query = f"SELECT {columns_to_display_str} FROM {selected_dataset}"
    count_query = f"SELECT COUNT(*) AS count FROM {selected_dataset}"
    if where_sql:
        base_query += f" WHERE {where_sql}"
        count_query += f" WHERE {where_sql}"

    return FilterQueryContext(
        dataset=selected_dataset,
        base_query=base_query,
        count_query=count_query,
        params=all_params,
        display_column_details=display_column_details,
        order_by_col=order_by_col,
    )


def _stream_prefixed_rows(
    context: FilterQueryContext,
    payload: Payload,
    batch_size: int = 10_000,
) -> Iterator[Dict[str, Any]]:
    """Yield dataset-prefixed rows directly from DuckDB in bounded batches."""
    query = _append_order_clause(context.base_query, payload, context.order_by_col)
    conn = duckdb.connect(_settings.duckdb_path, read_only=True)
    try:
        conn.execute("PRAGMA threads = 4")
        conn.execute("PRAGMA memory_limit = '2GB'")
        cursor = conn.execute(query, context.params)
        raw_columns = [desc[0] for desc in cursor.description]
        prefixed_columns = [f"{context.dataset}-{col}" for col in raw_columns]

        while True:
            chunk = cursor.fetchmany(batch_size)
            if not chunk:
                break
            for row in chunk:
                yield {
                    col_name: _normalize_value(value)
                    for col_name, value in zip(prefixed_columns, row)
                }
    finally:
        conn.close()


def filter_amr_records(payload: Payload, db: duckdb.DuckDBPyConnection):
    """Fetch a single page of AMR results for UI consumption."""
    context = _build_filter_query_context(payload, db)
    page = payload.page or 1
    per_page = payload.per_page or 100

    try:
        logger.info(f"selected_filters: {payload.selected_filters}")
        logger.info(f"count_query: {context.count_query}")

        total_hits = db.execute(context.count_query, context.params).fetchone()[0]

        offset = (page - 1) * per_page
        paginated_query = _append_order_clause(context.base_query, payload, context.order_by_col)
        paginated_query += f" LIMIT {per_page} OFFSET {offset}"
        logger.info(f"base_query: {paginated_query}")
        logger.info(f"parameters: {context.params}")

        res_df = db.execute(paginated_query, context.params).fetchdf()
        res_df = res_df.replace({np.nan: None, np.inf: None, -np.inf: None})
        res_df = res_df.add_prefix(f"{context.dataset}-")
        result = [serialize_amr_record(row, context.display_column_details) for _, row in res_df.iterrows()]

        return {
            "meta": {
                "total_hits": total_hits,
                "page": page,
                "per_page": per_page,
            },
            "data": result
        }

    except Exception as e:
        logger.error(f"Database query failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database query failed, see the logs for more details")


def flatten_record(record):
    """Convert list-of-dicts into {column_id: value}."""
    return {entry["column_id"]: entry.get("value") for entry in record}


def stream_csv(rows: Iterable[Dict[str, Any]]):
    """Generate CSV chunks progressively to avoid loading everything in memory."""
    iterator = iter(rows)
    try:
        first_row = next(iterator)
    except StopIteration:
        yield "id\n".encode("utf-8")
        return

    buffer = io.StringIO()
    headers = list(first_row.keys())
    writer = csv.DictWriter(buffer, fieldnames=headers)
    writer.writeheader()

    for i, row in enumerate(chain([first_row], iterator), start=1):
        writer.writerow(row)
        if buffer.tell() > 64 * 1024 or i % 500 == 0:
            chunk = buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)
            yield chunk.encode("utf-8")

    remaining = buffer.getvalue()
    if remaining:
        yield remaining.encode("utf-8")


def stream_json_rows(rows: Iterable[Dict[str, Any]]):
    """Stream JSON array chunks without loading everything into memory."""
    iterator = iter(rows)
    yield b"["
    first = True
    for row in iterator:
        chunk = json.dumps(row, ensure_ascii=False)
        if first:
            first = False
        else:
            yield b","
        yield chunk.encode("utf-8")
    yield b"]"


def fetch_filtered_records(payload: Payload, scope, file_format, db: duckdb.DuckDBPyConnection):
    """Download filtered AMR records in CSV or JSON format."""
    scope = (scope or "all").lower()
    if scope not in {"page", "all"}:
        raise HTTPException(status_code=400, detail="scope must be 'page' or 'all'")

    if scope == "page":
        data = filter_amr_records(payload, db)["data"]
        if not data:
            raise HTTPException(status_code=404, detail="No data found for the given filters")

        flat_results = [flatten_record(r) for r in data]
        if file_format == "json":
            content = json.dumps(flat_results, ensure_ascii=False, indent=2)
            file_like = io.BytesIO(content.encode("utf-8"))
            return StreamingResponse(
                file_like,
                media_type="application/json",
                headers={"Content-Disposition": "attachment; filename=amr_records.json"}
            )

        return StreamingResponse(
            stream_csv(flat_results),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=amr_records.csv"}
        )

    # scope == "all" - true streaming without loading the full dataset
    context = _build_filter_query_context(payload, db)
    total_hits = db.execute(context.count_query, context.params).fetchone()[0]
    if total_hits == 0:
        raise HTTPException(status_code=404, detail="No data found for the given filters")

    row_iter = _stream_prefixed_rows(context, payload)
    if file_format == "json":
        return StreamingResponse(
            stream_json_rows(row_iter),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=amr_records.json"}
        )

    return StreamingResponse(
        stream_csv(row_iter),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=amr_records.csv"}
    )
