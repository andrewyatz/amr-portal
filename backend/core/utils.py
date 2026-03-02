import math
from typing import Any


def _sanitise_nan(value: Any) -> Any:
    """Convert NaN float values to None for JSON compatibility."""
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def query_to_records(db, sql: str) -> list[dict[str, Any]]:
    """Run a SQL query and return a list of dict records.

    Args:
        db: Database connection object exposing `.query(sql).fetchdf()`.
        sql: SQL query to execute.

    Returns:
        List[Dict[str, Any]]: Each row represented as a dictionary.
    """
    records = db.query(sql).fetchdf().to_dict(orient="records")
    return [
        {k: _sanitise_nan(v) for k, v in row.items()}
        for row in records
    ]