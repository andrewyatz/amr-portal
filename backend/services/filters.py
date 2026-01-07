import csv
import io
import json
import math
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
from backend.core.utils import parse_location, bins_for_range_extended

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)
_settings = get_settings()

# Cache display columns per view_id so repeated downloads do not incur extra metadata queries.
_display_columns_cache: Dict[int, Any] = {}


@dataclass
class FilterQueryContext:
    """Container for all SQL/query metadata needed to stream AMR records."""

    dataset: str
    base_query: str
    count_query: str
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
        return set(columns_result["name"].tolist())
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Failed to get columns for table: {table_name}"
        )


def check_selected_filters(grouped_filters, valid_columns):
    """Verify that all requested filter columns exist within the dataset schema.

    Args:
        grouped_filters (dict): Mapping of column name to selected values.
        valid_columns (set[str]): Columns available on the dataset.

    Returns:
        bool: True when every requested column exists, False otherwise.
    """
    if set(grouped_filters).issubset(valid_columns):
        return True
    return False


def get_dataset_from_view(view_id: int, db: duckdb.DuckDBPyConnection):
    """Resolve the dataset backing a view_id.

    Args:
        view_id (int): View identifier coming from the UI configuration.
        db (duckdb.DuckDBPyConnection): Database connection.

    Returns:
        str: Dataset/table name associated with the view.

    Raises:
        HTTPException: If the lookup fails.
    """
    dataset_from_view_query = f"SELECT DISTINCT (dataset_name) FROM view_categories WHERE view_id = {view_id};"
    try:
        dataset = db.execute(dataset_from_view_query).fetchone()[0]
        return dataset
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Failed to get dataset from view ID: {view_id}"
        )


def get_display_column_details(view_id: int, db: duckdb.DuckDBPyConnection):
    """Return per-column metadata for a view, caching results for reuse.

    Args:
        view_id (int): Requested view identifier.
        db (duckdb.DuckDBPyConnection): Database connection.

    Returns:
        pandas.DataFrame: Column metadata including fullname, name, type, etc.

    Raises:
        HTTPException: If the metadata query fails.
    """

    if view_id in _display_columns_cache:
        return _display_columns_cache[view_id].copy()

    columns_to_display_query = f"""
        SELECT cd.fullname, cd.name, cd.type, cd.sortable, cd.url, cd.delimiter
        FROM view as v
            JOIN view_column vc on v.view_id = vc.view_id
            JOIN column_definition cd on vc.column_id = cd.column_id
        WHERE v.view_id = {view_id}
        ORDER BY vc.rank;
    """
    try:
        columns = db.execute(columns_to_display_query).fetchdf()
        _display_columns_cache[view_id] = columns
        return columns.copy()
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to get columns to display from view ID: {view_id}",
        )


def quote_name(item_name: str) -> str:
    """Quote a name for DuckDB SQL usage.

    Args:
        item_name (str): Unquoted identifier.

    Returns:
        str: Identifier quoted with double quotes.
    """
    return f'"{item_name}"'


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


def _append_order_clause(
    query: str, payload: Payload, order_by_col: Optional[str]
) -> str:
    """Add an ORDER BY clause if the payload specifies a sortable column.

    Args:
        query (str): Base SQL query.
        payload (Payload): Request payload that may include order_by.
        order_by_col (Optional[str]): Sanitized column name or None.

    Returns:
        str: Query with ORDER BY appended when required.
    """
    if payload.order_by and order_by_col:
        return f"{query} ORDER BY {quote_name(order_by_col)} {payload.order_by.order}"
    return query


def _build_filter_query_context(
    payload: Payload, db: duckdb.DuckDBPyConnection
) -> FilterQueryContext:
    """Pre-compute shared SQL fragments/metadata for both paged and streaming exports.

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
            status_code=400, detail="Please specify a view ID to filter by."
        )

    # Now we use the selected view to infer which dataset to query data from
    selected_dataset = get_dataset_from_view(selected_view_id, db)

    valid_columns = get_table_columns(selected_dataset, db)
    # not all valid columns are eventually displayed
    # We need to keep only the ones we are interested
    columns_to_display = get_display_column_details(selected_view_id, db)

    # This will be used below in the SQL query to select only columns we are interested in
    # Properly quote column names for SQL query
    quoted_columns = [quote_name(col) for col in columns_to_display["name"]]
    columns_to_display_str = ", ".join(quoted_columns)

    # Build dict of column details for serializer
    columns_to_display_dict = columns_to_display.to_dict("records")
    display_column_details = {r["fullname"]: r for r in columns_to_display_dict}

    # group them together and trim the first dataset name part
    grouped_filters = {}

    for f in payload.selected_filters:
        if f.filter_type not in grouped_filters:
            grouped_filters[f.filter_type] = defaultdict(list)
        grouped_filters[f.filter_type][f.trimmed_category()].append(f.value)

    are_filters_valid = True
    for k, v in grouped_filters.items():
        # TODO skip this check for the location column since it is bogus. Datasets should have
        # a location column which is the combined values
        if k == "location":
            continue
        logger.info(f"Checking filters of type: {k}")
        are_filters_valid = check_selected_filters(grouped_filters[k], valid_columns)
    logger.info(f"are_filters_valid: {are_filters_valid}")
    logger.info(f"selected_view_id: {selected_view_id}")
    logger.info(f"selected_dataset: {selected_dataset}")
    logger.info(f"grouped_filters: {grouped_filters}")
    logger.info(f"quoted_columns: {quoted_columns}")

    if not are_filters_valid:
        raise HTTPException(
            status_code=400,
            detail="Something is wrong with the filters, double check the category values.",
        )

    order_by_col = None
    if payload.order_by:
        order_by_col = payload.order_by.trimmed_category()
        if order_by_col not in valid_columns:
            raise HTTPException(
                status_code=400, detail=f"Invalid order_by column: {order_by_col!r}"
            )

    where_clauses = []
    for filter_type, filters in grouped_filters.items():
        if filter_type == "in":
            # Convert list to SQL tuple syntax: ('value1', 'value2')
            for category, values in filters.items():
                quoted_values = [f"'{v}'" for v in values]
                tuple_clause = f"({', '.join(quoted_values)})"
                where_clauses.append(f"{category} IN {tuple_clause}")
        elif filter_type == "exact":
            for category, values in filters.items():
                for value in values:
                    where_clauses.append(f"{category} = '{value}'")
        elif filter_type == "like":
            for category, values in filters.items():
                for value in values:
                    where_clauses.append(f"{category} LIKE '{value}%'")
        elif filter_type == "location":
            for category, values in filters.items():
                for value in values:
                    try:
                        start, end, strand = parse_location(value)
                    except ValueError:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invalid location filter value: {value!r}",
                        )
                    bins = bins_for_range_extended((start - 1), end)
                    where_clauses.append(
                        f"{quote_name("region_start")} <= {end} AND {quote_name("region_end")} >= {start} AND {quote_name("_bin")} IN ({",".join(str(b) for b in bins)}) {f"AND {quote_name("strand")} ='{strand}'" if strand else ''}"
                    )
        elif filter_type == "list_contains":
            for category, values in filters.items():
                for value in values:
                    where_clauses.append(f"LIST_CONTAINS({category}, '{value}')")

    where_sql = " AND ".join(where_clauses)
    base_query = f"SELECT {columns_to_display_str} FROM {quote_name(selected_dataset)}"
    count_query = f"SELECT COUNT(*) AS count FROM {quote_name(selected_dataset)}"
    if where_sql:
        base_query += f" WHERE {where_sql}"
        count_query += f" WHERE {where_sql}"

    return FilterQueryContext(
        dataset=selected_dataset,
        base_query=base_query,
        count_query=count_query,
        display_column_details=display_column_details,
        order_by_col=order_by_col,
    )


def _stream_prefixed_rows(
    context: FilterQueryContext,
    payload: Payload,
    batch_size: int = 10_000,
) -> Iterator[Dict[str, Any]]:
    """Yield dataset-prefixed rows directly from DuckDB in bounded batches.

    Args:
        context (FilterQueryContext): Shared SQL/metadata.
        payload (Payload): Download request payload.
        batch_size (int): Number of rows to fetch per chunk from DuckDB.

    Yields:
        Dict[str, Any]: Sanitized row mapping, prefixed with dataset name to match UI expectations.
    """
    query = _append_order_clause(context.base_query, payload, context.order_by_col)
    # Use a dedicated connection so the FastAPI dependency can close cleanly while streaming continues.
    conn = duckdb.connect(_settings.duckdb_path, read_only=True)
    try:
        conn.execute("PRAGMA threads = 4")
        conn.execute("PRAGMA memory_limit = '2GB'")
        cursor = conn.execute(query)
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
    """Fetch a single page of AMR results for UI consumption.

    Args:
        payload (Payload): Request payload with pagination/filtering details.
        db (duckdb.DuckDBPyConnection): Database connection.

    Returns:
        dict: Paginated response matching the existing API contract.

    Raises:
        HTTPException: If DuckDB fails or invalid filters/order_by are provided.
    """
    context = _build_filter_query_context(payload, db)
    page = payload.page or 1
    per_page = payload.per_page or 100

    try:
        logger.info(f"selected_filters: {payload.selected_filters}")
        logger.info(f"count_query: {context.count_query}")

        total_hits = db.execute(context.count_query).fetchone()[0]

        offset = (page - 1) * per_page
        paginated_query = _append_order_clause(
            context.base_query, payload, context.order_by_col
        )
        paginated_query += f" LIMIT {per_page} OFFSET {offset}"
        logger.info(f"base_query: {paginated_query}")

        res_df = db.execute(paginated_query).fetchdf()
        res_df = res_df.replace({np.nan: None, np.inf: None, -np.inf: None})
        res_df = res_df.add_prefix(f"{context.dataset}-")
        result = [
            serialize_amr_record(row, context.display_column_details)
            for _, row in res_df.iterrows()
        ]

        return {
            "meta": {
                "total_hits": total_hits,
                "page": page,
                "per_page": per_page,
            },
            "data": result,
        }

    except Exception as e:
        logger.error(f"Database query failed: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Database query failed, see the logs for more details",
        )


def flatten_record(record):
    """Convert list-of-dicts into {column_id: value}.

    Args:
        record (list[dict]): Serialized AMR record from the UI serializer.

    Returns:
        dict: Flattened mapping used by CSV/JSON streaming helpers.
    """
    return {entry["column_id"]: entry.get("value") for entry in record}


def stream_csv(rows: Iterable[Dict[str, Any]]):
    """Generate CSV chunks progressively to avoid loading everything in memory.

    Args:
        rows (Iterable[Dict[str, Any]]): Iterator of flattened records.

    Yields:
        bytes: UTF-8 encoded CSV chunks with headers emitted once.
    """
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
        # Sends data when buffer reaches ~64KB or every 500 rows
        if buffer.tell() > 64 * 1024 or i % 500 == 0:
            chunk = buffer.getvalue()     # Get current buffer content
            buffer.seek(0)                # Move to start
            buffer.truncate(0)            # Clear buffer
            yield chunk.encode("utf-8")   # Send chunk as bytes

    # Send any remaining data
    remaining = buffer.getvalue()
    if remaining:
        yield remaining.encode("utf-8")


def stream_json_rows(rows: Iterable[Dict[str, Any]]):
    """Stream JSON array chunks without loading everything into memory.

    Args:
        rows (Iterable[Dict[str, Any]]): Iterator of flattened records.

    Yields:
        bytes: UTF-8 encoded JSON fragments representing an array.
    """
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


def fetch_filtered_records(
    payload: Payload, scope, file_format, db: duckdb.DuckDBPyConnection
):
    """Download filtered AMR records in CSV or JSON format.

    Args:
        payload (Payload): Request payload matching the POST body schema.
        scope (str): Either "page" or "all" to control pagination versus full export.
        file_format (str): Either "csv" or "json".
        db (duckdb.DuckDBPyConnection): Database connection.

    Returns:
        StreamingResponse: Response that streams either CSV or JSON data.

    Raises:
        HTTPException: If scope/file_format are invalid or no rows match the filters.
    """
    scope = (scope or "all").lower()
    if scope not in {"page", "all"}:
        raise HTTPException(status_code=400, detail="scope must be 'page' or 'all'")

    if scope == "page":
        data = filter_amr_records(payload, db)["data"]
        if not data:
            raise HTTPException(
                status_code=404, detail="No data found for the given filters"
            )

        flat_results = [flatten_record(r) for r in data]
        if file_format == "json":
            content = json.dumps(flat_results, ensure_ascii=False, indent=2)
            file_like = io.BytesIO(content.encode("utf-8"))
            return StreamingResponse(
                file_like,
                media_type="application/json",
                headers={
                    "Content-Disposition": "attachment; filename=amr_records.json"
                },
            )

        return StreamingResponse(
            stream_csv(flat_results),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=amr_records.csv"},
        )

    # scope == "all" - true streaming without loading the full dataset
    context = _build_filter_query_context(payload, db)
    total_hits = db.execute(context.count_query).fetchone()[0]
    if total_hits == 0:
        raise HTTPException(
            status_code=404, detail="No data found for the given filters"
        )

    # Stream rows straight from DuckDB so responses for 100k+ rows start immediately.
    row_iter = _stream_prefixed_rows(context, payload)
    if file_format == "json":
        return StreamingResponse(
            stream_json_rows(row_iter),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=amr_records.json"},
        )

    return StreamingResponse(
        stream_csv(row_iter),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=amr_records.csv"},
    )
