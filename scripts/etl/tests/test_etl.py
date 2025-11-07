import pytest
import json
from pathlib import Path
import sys
from etl.etl import run_etl
import duckdb
import pandas as pd
from pandas.testing import assert_frame_equal


@pytest.fixture
def data_dir():
    return Path(__file__).resolve().parent / "data"


@pytest.fixture
def results_dir():
    return Path(__file__).resolve().parent / "data"


@pytest.fixture
def config_dir():
    return Path(__file__).resolve().parent / "config"


@pytest.fixture
def schema_dir():
    return Path(__file__).resolve().parent.parent / "schema"


def setup_data_json(tmp_path, config_dir, data_dir):
    rewritten_json_path = tmp_path / "data.json"
    with open(config_dir / "data.json", "rt") as fh:
        blob = json.load(fh)
        blob[0]["path"] = str(data_dir / "test.csv")
        with open(rewritten_json_path, "wt") as wfh:
            json.dump(blob, wfh, indent=2)

    return rewritten_json_path


def test_run_etl(monkeypatch, tmp_path, config_dir, data_dir, schema_dir, results_dir):
    rewritten_data_json_path = setup_data_json(tmp_path, config_dir, data_dir)
    release = "test_v1"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "main.py",
            "--release",
            release,
            "--config",
            str(config_dir / "config.json"),
            "--data",
            str(rewritten_data_json_path),
            "--schema",
            str(schema_dir),
        ],
    )
    monkeypatch.chdir(tmp_path)
    run_etl()
    print(tmp_path)

    duckdb_path = tmp_path / release / "amr_test_v1.duckdb"
    assert duckdb_path.exists()
    assert duckdb_path.is_file()

    # input()
    con = duckdb.connect(duckdb_path)
    # Assert the test table
    _assert_table(
        results_dir, con, "test", numeric_cols=["start", "stop", "measurement"]
    )

    # Now assert the ancillary tables
    remaining_tables = [
        "category",
        "category_group",
        "category_group_category",
        "column_definition",
        "dataset",
        "dataset_column",
        "filter",
        "release",
        "view",
        "view_categories",
        "view_categories_json",
        "view_column",
    ]
    _assert_table(
        results_dir, con, "test", numeric_cols=["start", "stop", "measurement"]
    )


def _assert_table(results_dir, con, table_name, numeric_cols=[]):
    expected = pd.read_csv(results_dir / f"{table_name}.csv")
    actual = con.execute(
        f"select {",".join(expected.columns)} from {table_name}"
    ).fetchdf()
    for col in numeric_cols:
        expected[col] = pd.to_numeric(expected[col])
        actual[col] = pd.to_numeric(actual[col])
    assert_frame_equal(actual, expected, check_dtype=False)
