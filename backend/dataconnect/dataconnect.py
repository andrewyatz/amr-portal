import duckdb
import re
from fastapi import APIRouter, HTTPException, Depends, Request

import sqlglot
import sqlglot.expressions as exp

from backend.core.database import get_db_connection
from backend.models.dataconnect import DataConnectQuery
from backend.services.filters import fetch_filters, get_dataset_from_view

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
    service_info = {
        "id": "example-dataconnect",
        "name": "Example GA4GH Data Connect Service",
        "type": {"group": "org.ga4gh", "artifact": "data-connect", "version": "1.2.0"},
        "description": "A GA4GH Data Connect service providing access to example datasets.",
        "organization": {"name": "Example Organization", "url": "https://example.org"},
        "contactUrl": "mailto:support@example.org",
        "documentationUrl": "https://example.org/docs/dataconnect",
        "createdAt": "2024-01-01T00:00:00Z",
        "updatedAt": "2024-12-01T00:00:00Z",
        "environment": "production",
        "version": "0.1.0",
    }
    return service_info


@router.get("/tables")
def list_tables(db: duckdb.DuckDBPyConnection = Depends(get_db_connection)):
    filters = fetch_filters(db)
    tables = []
    for view in filters["filterViews"]:
        view_id = view["id"]
        table_name = _view_id_to_dataset_name(db, int(view_id))
        tables.append({"name": table_name, "data_model": f"/table/{table_name}/info"})
    return {"tables": tables}


@router.get("/table/{table_name}/info")
def get_table_info(
    table_name: str, db: duckdb.DuckDBPyConnection = Depends(get_db_connection)
):
    query = f"SELECT * from {table_name} LIMIT 1"
    rel = db.sql(query)
    schema = _executed_query_to_json_schema(rel)
    data_model = {
        "description": f"Automatically generated schema for table '{table_name}'",
        "$schema": "http://json-schema.org/draft-07/schema#",
        "properties": schema,
    }
    info = {"name": table_name, "data_model": data_model}
    return info

@router.get("/table/{table_name}/data")
def get_table_data(
    request: Request,
    table_name: str,
    db: duckdb.DuckDBPyConnection = Depends(get_db_connection),
    page_size: int = 100,
    page: int = 0,
):
    offset = page * page_size
    
    print(offset, page_size, page)
    
    total_count = db.execute(
        f"SELECT COUNT(*) FROM {table_name}"
    ).fetchone()[0]
    
    if offset >= total_count:
        raise HTTPException(status_code=404, detail="Page out of range")
    
    sql = f"SELECT * FROM {table_name} LIMIT ? OFFSET ?"
    response = query_table(DataConnectQuery(query=sql, parameters=[page_size, offset]), db)
    next_page = request.url.include_query_params(page=page + 1, page_size=page_size)
    response["pagination"] = {
        "next_page_url": str(next_page)
    }
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
            status_code=400, detail=f"Query execution error. Cannot parse query: {e}"
        )

    if not isinstance(parsed, exp.Select):
        raise HTTPException(
            status_code=400, detail="Parsed SQL is not a SELECT statement."
        )

    column_schema_map = {}
    for expression in parsed.expressions:
        output_name = expression.alias_or_name
        # Unwrap Alias to find the function call
        child_expr = (
            expression.this if isinstance(expression, exp.Alias) else expression
        )
        if isinstance(child_expr, exp.Func) and child_expr.this.lower() == "ga4gh_type":
            args = child_expr.args.get("expressions") or child_expr.expressions
            if len(args) >= 2:
                # The second argument is the schema reference
                schema_ref_expr = args[1]
                if isinstance(schema_ref_expr, exp.Literal):
                    column_schema_map[output_name] = schema_ref_expr.this
                elif isinstance(schema_ref_expr, exp.Column):
                    column_schema_map[output_name] = schema_ref_expr.this.this

    try:
        db.execute("CREATE OR REPLACE TEMPORARY MACRO ga4gh_type(val, schema_ref) AS val")
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


def _name_to_view_id(db: duckdb.DuckDBPyConnection, name: str) -> int:
    filters = fetch_filters(db)
    for view in filters["filterViews"]:
        if view["url_name"] == name:
            return int(view["id"])
    raise HTTPException(
        status_code=404,
        detail=f"Table '{name}' not found. Cannot map to internal view ID.",
    )


def _view_id_to_dataset_name(db: duckdb.DuckDBPyConnection, view_id: int) -> str:
    return get_dataset_from_view(db=db, view_id=view_id)


def _executed_query_to_json_schema(result: duckdb.DuckDBPyRelation):
    schema = {
        "type": "object",
        "properties": {}
    }

    for col in result.description:
        column_name = col[0]
        duckdb_type = col[1]
        prop = _duckdb_type_to_json_schema(duckdb_type)
        schema["properties"][column_name] = prop
    
    return schema

def _duckdb_type_to_json_schema(duckdb_type) -> dict:
        # Normalize type name
        base_type = re.split(r"\(|<", str(duckdb_type))[0].upper()

        json_type, json_format = DUCKDB_TO_JSON_SCHEMA.get(
            base_type, ("string", None)
        )

        prop = {"type": json_type}
        if json_format:
            prop["format"] = json_format
        return prop
