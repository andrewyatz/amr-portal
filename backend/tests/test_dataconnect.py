import tempfile
from pathlib import Path

import duckdb
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.dataconnect.dataconnect import router


@pytest.fixture()
def test_db(tmp_path):
    """Create a temporary DuckDB with v2 schema and sample data."""
    db_path = tmp_path / "test.duckdb"
    conn = duckdb.connect(str(db_path))

    # Create v2 schema tables
    conn.execute("""
        CREATE TABLE "view" (
            view_id INTEGER PRIMARY KEY,
            id VARCHAR NOT NULL UNIQUE,
            url_name VARCHAR NOT NULL UNIQUE,
            "name" VARCHAR NOT NULL,
            "source" VARCHAR NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE view_column (
            view_column_id INTEGER PRIMARY KEY,
            view_id INTEGER NOT NULL,
            "name" VARCHAR NOT NULL,
            "label" VARCHAR NOT NULL,
            "type" VARCHAR NOT NULL,
            sortable BOOLEAN NOT NULL DEFAULT true,
            url VARCHAR,
            "delimiter" VARCHAR,
            hidden BOOLEAN NOT NULL DEFAULT false,
            rank INTEGER NOT NULL,
            enable_by_default BOOLEAN NOT NULL DEFAULT true,
            FOREIGN KEY (view_id) REFERENCES "view"(view_id),
            UNIQUE (view_id, "name")
        )
    """)

    # Insert a view
    conn.execute("""
        INSERT INTO "view" VALUES
            (1, 'test_view', 'test-view', 'Test View', 'test_data')
    """)

    # Insert columns with different types
    conn.execute("""
        INSERT INTO view_column VALUES
            (1, 1, 'sample_id', 'Sample ID', 'link', true, 'https://example.org/samples/{}', NULL, false, 1, true),
            (2, 1, 'organism', 'Organism', 'string', true, NULL, NULL, false, 2, true),
            (3, 1, 'genes', 'Gene List', 'array-link', false, 'https://example.org/genes/{}', ',', false, 3, true),
            (4, 1, 'internal_id', 'Internal', 'string', false, NULL, NULL, true, 4, false)
    """)

    # Create the actual data table referenced by view.source
    conn.execute("""
        CREATE TABLE test_data (
            sample_id VARCHAR,
            organism VARCHAR,
            genes VARCHAR,
            internal_id VARCHAR,
            extra_col INTEGER
        )
    """)
    conn.execute("""
        INSERT INTO test_data VALUES
            ('SAM001', 'E. coli', 'geneA,geneB', 'INT1', 42),
            ('SAM002', 'S. aureus', 'geneC', 'INT2', 99),
            ('SAM003', 'K. pneumoniae', 'geneA,geneD,geneE', 'INT3', 7)
    """)

    conn.close()
    return db_path


@pytest.fixture()
def client(test_db):
    """Create a FastAPI TestClient with the dataconnect router."""
    app = FastAPI()
    app.include_router(router, prefix="/dataconnect")

    def _get_db():
        conn = duckdb.connect(str(test_db), read_only=True)
        try:
            yield conn
        finally:
            conn.close()

    from backend.core.database import get_db_connection
    app.dependency_overrides[get_db_connection] = _get_db

    return TestClient(app)


class TestServiceInfo:
    def test_returns_ga4gh_metadata(self, client):
        resp = client.get("/dataconnect/service-info")
        assert resp.status_code == 200
        body = resp.json()
        assert body["type"]["artifact"] == "data-connect"
        assert body["type"]["group"] == "org.ga4gh"
        assert "id" in body
        assert "name" in body


class TestListTables:
    def test_returns_tables(self, client):
        resp = client.get("/dataconnect/tables")
        assert resp.status_code == 200
        body = resp.json()
        assert "tables" in body
        assert len(body["tables"]) == 1
        assert body["tables"][0]["name"] == "test_data"
        assert "data_model" in body["tables"][0]

    def test_data_model_urls_are_fully_qualified(self, client):
        resp = client.get("/dataconnect/tables")
        body = resp.json()
        url = body["tables"][0]["data_model"]
        # Must include the /dataconnect prefix and be a full URL
        assert "/dataconnect/table/test_data/info" in url
        assert url.startswith("http")


class TestTableInfo:
    def test_returns_schema(self, client):
        resp = client.get("/dataconnect/table/test_data/info")
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "test_data"
        props = body["data_model"]["properties"]

        # Columns from view_column metadata
        assert props["sample_id"]["type"] == "string"
        assert props["sample_id"]["x-url-template"] == "https://example.org/samples/{}"
        assert props["organism"]["description"] == "Organism"
        assert props["genes"]["x-delimiter"] == ","
        assert props["genes"]["x-url-template"] == "https://example.org/genes/{}"

        # Hidden column still included (Data Connect exposes all)
        assert "internal_id" in props

        # extra_col not in view_column but in data table — should be present via fallback
        assert "extra_col" in props
        assert props["extra_col"]["type"] == "integer"

    def test_nonexistent_table(self, client):
        resp = client.get("/dataconnect/table/nonexistent/info")
        assert resp.status_code == 400


class TestTableData:
    def test_returns_paginated_data(self, client):
        resp = client.get("/dataconnect/table/test_data/data?page_size=2&page=0")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2
        assert "pagination" in body
        assert "next_page_url" in body["pagination"]

    def test_returns_all_columns(self, client):
        resp = client.get("/dataconnect/table/test_data/data?page_size=10&page=0")
        body = resp.json()
        row = body["data"][0]
        assert "sample_id" in row
        assert "organism" in row
        assert "extra_col" in row
        assert "internal_id" in row

    def test_page_out_of_range(self, client):
        resp = client.get("/dataconnect/table/test_data/data?page_size=10&page=100")
        assert resp.status_code == 404

    def test_nonexistent_table(self, client):
        resp = client.get("/dataconnect/table/nonexistent/data")
        assert resp.status_code == 400


class TestQuery:
    def test_simple_select(self, client):
        resp = client.post(
            "/dataconnect/query",
            json={"query": "SELECT sample_id, organism FROM test_data LIMIT 2"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2
        assert "sample_id" in body["data"][0]
        assert "organism" in body["data"][0]
        assert "data_model" in body
        assert "properties" in body["data_model"]

    def test_query_with_parameters(self, client):
        resp = client.post(
            "/dataconnect/query",
            json={
                "query": "SELECT * FROM test_data WHERE extra_col > ?",
                "parameters": [10],
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2  # SAM001 (42) and SAM002 (99)

    def test_non_select_rejected(self, client):
        resp = client.post(
            "/dataconnect/query",
            json={"query": "DROP TABLE test_data"},
        )
        assert resp.status_code == 400

    def test_invalid_sql_rejected(self, client):
        resp = client.post(
            "/dataconnect/query",
            json={"query": "NOT VALID SQL AT ALL ???"},
        )
        assert resp.status_code == 400

    def test_ga4gh_type_macro(self, client):
        resp = client.post(
            "/dataconnect/query",
            json={
                "query": "SELECT ga4gh_type(sample_id, 'http://example.org/schema#sample') AS sample_id FROM test_data LIMIT 1"
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        props = body["data_model"]["properties"]
        assert props["sample_id"]["$ref"] == "http://example.org/schema#sample"


class TestOptionalConfig:
    def test_dataconnect_disabled_by_default(self):
        """When enable_dataconnect is False, routes should not be registered."""
        from backend.core.config import Settings

        # Default value should be False
        assert Settings.model_fields["enable_dataconnect"].default is False

    def test_dataconnect_routes_not_present_when_disabled(self, test_db):
        """Build an app without dataconnect and verify routes are absent."""
        app = FastAPI()
        # Only include the api router, not dataconnect
        from backend.api.endpoints import router as api_router
        app.include_router(api_router, prefix="/api")

        test_client = TestClient(app)
        resp = test_client.get("/dataconnect/tables")
        assert resp.status_code == 404

    def test_dataconnect_routes_present_when_enabled(self, client):
        """The client fixture includes the dataconnect router."""
        resp = client.get("/dataconnect/tables")
        assert resp.status_code == 200
