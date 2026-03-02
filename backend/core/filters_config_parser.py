from __future__ import annotations

import json
from typing import Any, Iterable
from collections import defaultdict, OrderedDict

import duckdb
from backend.core.utils import query_to_records


def _resolve_column_name(filter_name: str, query_columns: Any) -> str:
    """Resolve the actual data column name from query_columns or filter_name.

    If query_columns specifies a 'column' key, use that; otherwise
    fall back to filter_name as the column name.
    """
    if query_columns and isinstance(query_columns, dict) and "column" in query_columns:
        return query_columns["column"]
    return filter_name


def _build_filter_categories(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Transform filter_config + view_filter_value rows into the filterCategories structure.

    Each filter is keyed by its prefixed id (source-column_name) to maintain
    the frontend's expected column identity format. The column name is resolved
    from query_columns when filter IDs differ from column names.

    Args:
        rows: Iterable of dict rows from the filter query.

    Returns:
        Dict keyed by prefixed column id -> category payload with filters and type info.
    """
    categories: dict[str, dict[str, Any]] = OrderedDict()

    for r in rows:
        source = r["source"]
        filter_name = r["filter_name"]

        query_columns = r.get("query_columns")
        if query_columns and isinstance(query_columns, str):
            query_columns = json.loads(query_columns)

        # Use the actual column name for the category ID prefix (frontend compatibility)
        col_name = _resolve_column_name(filter_name, query_columns)
        cat_id = f"{source}-{col_name}"

        if cat_id not in categories:
            categories[cat_id] = {
                "id": cat_id,
                "label": r["filter_label"],
                "dataset": source,
                "filter_type": r["filter_type"],
                "match_type": r.get("match_type"),
                "min": r.get("min"),
                "max": r.get("max"),
                "query_columns": query_columns,
                "regex": r.get("regex"),
                "filters": [],
            }

        # Only add filter values for select_list type
        if r.get("filter_value") is not None:
            categories[cat_id]["filters"].append(
                {
                    "label": r["value_label"],
                    "value": r["filter_value"],
                }
            )

    return categories


def _build_columns_per_view(db):
    """Builds a dictionary mapping view names to their column configurations.

    Queries view_column directly (new schema has all metadata inline,
    no join to column_definition needed). Adds source prefix to column
    name for frontend compatibility.

    Args:
        db: Database connection object.

    Returns:
        A dictionary where keys are view names and values are lists of column
        configuration dictionaries.
    """
    columns_per_view_query = """
        SELECT
            v.name AS view_name,
            CONCAT(v.source, '-', vc.name) AS id,
            vc.label,
            vc.sortable,
            vc.rank,
            vc.enable_by_default,
            vc.hidden
        FROM view AS v
            JOIN view_column vc ON v.view_id = vc.view_id
        WHERE vc.hidden = false
        ORDER BY vc.rank
    """

    columns_per_view = query_to_records(db, columns_per_view_query)
    columns_grouped_per_view = defaultdict(list)

    for col in columns_per_view:
        columns_grouped_per_view[col["view_name"]].append(col)

    return columns_grouped_per_view


def _build_filter_views(db, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Transform filter_config rows into the filterViews structure.

    Uses view_filter_group rank for ordering instead of the old
    categoryGroups/otherCategoryGroups split with is_primary flag.

    Args:
        db: Database connection object.
        rows: Iterable of dict rows from the filter_config view.

    Returns:
        List of view dictionaries ordered by view_dbid.
    """
    # Build views keyed by view_id to avoid assuming contiguous IDs.
    views: dict[Any, dict[str, Any]] = OrderedDict()
    # Per-view index of groups to avoid O(n^2) lookups when appending.
    per_view_group_index: dict[Any, dict[tuple[str, bool], int]] = defaultdict(dict)

    for r in rows:
        vid = r["view_dbid"]
        source = r["source"]

        if vid not in views:
            views[vid] = {
                "id": vid,
                "name": r["view_name"],
                "url_name": r["view_url_name"],
                "filterGroups": [],
                "columns": [],
            }

        group_id = r["group_id"]
        groups = per_view_group_index[vid]

        if group_id not in groups:
            groups[group_id] = {
                "id": group_id,
                "label": r["group_label"],
                "rank": r["group_rank"],
                "categories": [],
            }

        # Resolve actual column name for the prefixed id (frontend compatibility)
        query_columns = r.get("query_columns")
        if query_columns and isinstance(query_columns, str):
            query_columns = json.loads(query_columns)
        col_name = _resolve_column_name(r["filter_name"], query_columns)
        prefixed_filter_id = f"{source}-{col_name}"
        group = groups[group_id]
        if prefixed_filter_id not in group["categories"]:
            group["categories"].append(prefixed_filter_id)

    # Attach sorted groups to views
    for vid, groups in per_view_group_index.items():
        sorted_groups = sorted(groups.values(), key=lambda g: g["rank"])
        views[vid]["filterGroups"] = sorted_groups

    # Add columns per view
    columns_per_view = _build_columns_per_view(db)
    for view_id, view in views.items():
        view_name = view["name"]
        if view_name in columns_per_view:
            # Get columns and remove 'view_name' from each column dict
            columns = columns_per_view[view_name]
            for column in columns:
                column.pop("view_name", None)  # Remove view_name if it exists
            view["columns"] = columns
        else:
            # Handle case where view name doesn't exist
            view["columns"] = []

    # Return views ordered by view_id (insertion order already reflects scan order,
    # but sorting is safer if the SQL loses ORDER BY in the future).
    sorted_response = [views[k] for k in sorted(views.keys())]
    return sorted_response


def build_filters_config(db: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Build the complete filters configuration document from the new v2 schema.

    Uses the filter_config and column_config convenience views along with
    view_filter_value for pre-computed filter options.

    Args:
        db: Database connection object.

    Returns:
        Dict with keys: filterCategories, filterViews, release.
    """
    # Categories: join filter_config with view_filter_value for select_list values
    filters_category_query = """
        SELECT
            fc.source,
            fc.filter_name,
            fc.filter_label,
            fc.filter_type,
            fc.match_type,
            fc.min,
            fc.max,
            fc.query_columns,
            fc.regex,
            fc.view_filter_id,
            fv.value AS filter_value,
            fv.label AS value_label
        FROM filter_config fc
            LEFT JOIN view_filter_value fv ON fc.view_filter_id = fv.view_filter_id
        ORDER BY fc.view_filter_id, fv.value
    """
    category_rows = query_to_records(db, filters_category_query)
    filter_categories = _build_filter_categories(category_rows)

    # Views: use filter_config directly
    filters_view_query = """
        SELECT
            view_dbid,
            view_id,
            view_url_name,
            view_name,
            source,
            group_id,
            group_label,
            group_rank,
            filter_rank,
            filter_name,
            filter_label,
            filter_type,
            query_columns
        FROM filter_config
        ORDER BY view_dbid, group_rank, filter_rank
    """
    view_rows = query_to_records(db, filters_view_query)
    filter_views = _build_filter_views(db, view_rows)

    # Release
    release_query = "SELECT release_label as label FROM release"
    release_rows = query_to_records(db, release_query)
    release = release_rows[0] if release_rows else None

    return {
        "filterCategories": filter_categories,
        "filterViews": filter_views,
        "release": release
    }
