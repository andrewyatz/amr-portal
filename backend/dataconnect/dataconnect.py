import re

import duckdb
import sqlglot
import sqlglot.expressions as exp
from fastapi import APIRouter, Depends, HTTPException, Request

from backend.core.database import get_db_connection
from backend.models.dataconnect import DataConnectQuery

router = APIRouter()

DUCKDB_TO_JSON_SCHEMA = {
    "BOOLEAN": ("boolean", None),
    "TINYINT": ("integer", None),
    "NUMBER": ("integer", None),
    "SMALLINT": ("integer", None),
    "INTEGER": ("integer", None),
    "BIGINT": ("integer", None),
    "UTINYINT": ("integer", None),
    "USMALLINT": ("integer", None),
    "UINTEGER": ("integer", None),
    "UBIGINT": ("integer", None),
    "FLOAT": ("number", None),
    "DOUBLE": ("number", None),
    "REAL": ("number", None),
    "DECIMAL": ("number", None),
    "VARCHAR": ("string", None),
    "CHAR": ("string", None),
    "TEXT": ("string", None),
    "DATE": ("string", "date"),
    "TIMESTAMP": ("string", "date-time"),
    "TIMESTAMP_TZ": ("string", "date-time"),
    "TIME": ("string", "time"),
    "UUID": ("string", "uuid"),
    "BLOB": ("string", "binary"),
    "JSON": ("object", None),
}


@router.get("/service-info")
def get_service_info():
    return {
        "id": "example-dataconnect",
        "name": "Example GA4GH Data Connect Service",
        "type": {
            "group": "org.ga4gh",
            "artifact": "data-connect",
            "version": "1.2.0",
        },
        "description": "A GA4GH Data Connect service providing access to example datasets.",
        "organization": {"name": "Example Organization", "url": "https://example.org"},
        "contactUrl": "mailto:support@example.org",
        "documentationUrl": "https://example.org/docs/dataconnect",
        "createdAt": "2024-01-01T00:00:00Z",
        "updatedAt": "2024-12-01T00:00:00Z",
        "environment": "production",
        "version": "0.1.0",
    }


@router.get("/tables")
def list_tables(
    request: Request,
    db: duckdb.DuckDBPyConnection = Depends(get_db_connection),
):
    views = db.execute("SELECT DISTINCT source FROM view").fetchall()
    tables = [
        {
            "name": row[0],
            "data_model": str(request.url_for("get_table_info", table_name=row[0])),
        }
        for row in views
    ]
    return {"tables": tables}


@router.get("/table/{table_name}/info")
def get_table_info(
    table_name: str,
    db: duckdb.DuckDBPyConnection = Depends(get_db_connection),
):
    _verify_table_exists(db, table_name)
    properties = _build_json_schema_from_metadata(db, table_name)
    data_model = {
        "description": f"Automatically generated schema for table '{table_name}'",
        "$schema": "http://json-schema.org/draft-07/schema#",
        "properties": properties,
    }
    return {"name": table_name, "data_model": data_model}


@router.get("/table/{table_name}/data")
def get_table_data(
    request: Request,
    table_name: str,
    db: duckdb.DuckDBPyConnection = Depends(get_db_connection),
    page_size: int = 100,
    page: int = 0,
):
    _verify_table_exists(db, table_name)
    offset = page * page_size
    total_count = db.execute(
        f"SELECT COUNT(*) FROM \"{table_name}\""
    ).fetchone()[0]

    if offset >= total_count:
        raise HTTPException(status_code=404, detail="Page out of range")

    sql = f"SELECT * FROM \"{table_name}\" LIMIT ? OFFSET ?"
    response = query_table(
        DataConnectQuery(query=sql, parameters=[page_size, offset]), db
    )
    next_page = request.url.include_query_params(page=page + 1, page_size=page_size)
    response["pagination"] = {"next_page_url": str(next_page)}
    return response


@router.post("/query")
def query_table(
    query: DataConnectQuery,
    db: duckdb.DuckDBPyConnection = Depends(get_db_connection),
):
    try:
        parsed = sqlglot.parse_one(query.query, read="duckdb")
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Query execution error. Cannot parse query: {e}",
        )

    if not isinstance(parsed, exp.Select):
        raise HTTPException(
            status_code=400, detail="Parsed SQL is not a SELECT statement."
        )

    column_schema_map = {}
    for expression in parsed.expressions:
        output_name = expression.alias_or_name
        child_expr = (
            expression.this if isinstance(expression, exp.Alias) else expression
        )
        if isinstance(child_expr, exp.Func) and child_expr.this.lower() == "ga4gh_type":
            args = child_expr.args.get("expressions") or child_expr.expressions
            if len(args) >= 2:
                schema_ref_expr = args[1]
                if isinstance(schema_ref_expr, exp.Literal):
                    column_schema_map[output_name] = schema_ref_expr.this
                elif isinstance(schema_ref_expr, exp.Column):
                    column_schema_map[output_name] = schema_ref_expr.this.this

    try:
        db.execute(
            "CREATE OR REPLACE TEMPORARY MACRO ga4gh_type(val, schema_ref) AS val"
        )
        rel = db.sql(query.query, params=query.parameters)
        columns = rel.columns
        results = rel.fetchall()

        response_data = []
        for row in results:
            response_data.append(dict(zip(columns, row)))

        properties = {}
        for i, col_name in enumerate(columns):
            if col_name in column_schema_map:
                properties[col_name] = {"$ref": column_schema_map[col_name]}
            else:
                prop = _duckdb_type_to_json_schema(rel.description[i][1])
                properties[col_name] = prop

        data_model = {
            "description": "Schema specified by query",
            "$schema": "http://json-schema.org/draft-07/schema#",
            "properties": properties,
        }

        return {"data_model": data_model, "data": response_data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Query execution error: {e}")


def _verify_table_exists(
    db: duckdb.DuckDBPyConnection, table_name: str
) -> str:
    result = db.execute(
        "SELECT source FROM view WHERE source = ?", (table_name,)
    ).fetchone()
    if not result:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to find the specified table: {table_name}",
        )
    return result[0]


def _build_json_schema_from_metadata(
    db: duckdb.DuckDBPyConnection, table_name: str
) -> dict:
    """Build JSON Schema properties from view_column metadata + DuckDB types.

    Columns with view_column metadata get enriched with descriptions and
    URL templates. Columns only in the data table fall back to DuckDB type
    mapping.
    """
    meta_rows = db.execute(
        """
        SELECT vc.name, vc.label, vc.type, vc.url, vc.delimiter
        FROM view_column vc
            JOIN view v ON vc.view_id = v.view_id
        WHERE v.source = ?
        ORDER BY vc.rank
        """,
        (table_name,),
    ).fetchall()

    duckdb_types = {
        r[0]: r[1]
        for r in db.execute(f'DESCRIBE "{table_name}"').fetchall()
    }

    properties = {}

    for name, label, app_type, url, delimiter in meta_rows:
        prop = _duckdb_type_to_json_schema(duckdb_types.get(name, "VARCHAR"))
        if label:
            prop["description"] = label
        if app_type in ("link", "labelled-link") and url:
            prop["x-url-template"] = url
        if app_type == "array-link":
            if url:
                prop["x-url-template"] = url
            if delimiter:
                prop["x-delimiter"] = delimiter
        properties[name] = prop

    for col_name, col_type in duckdb_types.items():
        if col_name not in properties:
            properties[col_name] = _duckdb_type_to_json_schema(col_type)

    return properties


def _duckdb_type_to_json_schema(duckdb_type) -> dict:
    base_type = re.split(r"\(|<", str(duckdb_type))[0].upper()
    json_type, json_format = DUCKDB_TO_JSON_SCHEMA.get(
        base_type, ("string", None)
    )
    prop = {"type": json_type}
    if json_format:
        prop["format"] = json_format
    return prop
