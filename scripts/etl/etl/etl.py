import sys
import os
import json

from etl.cli import get_cli_args
from etl.transform import transform_datasets
from etl.config import validate_config, validate_dataset
from etl.dataset import build_datasets
from etl.filters import generate_filters
from etl.processor import amr_release_to_duckdb

CONFIG_SCHEMA_FILE = "config.json"
DATA_SCHEMA_FILE = "dataset.json"


def etl_step_1(config: dict, data: dict, release_path: str, cli) -> (bool, str):
    """
    Validate config and dataset
    """
    # check schemas exist
    config_schema = os.path.join(cli.schema, CONFIG_SCHEMA_FILE)
    if not os.path.exists(config_schema):
        return (
            False,
            f"Unable to find config schema. Expected: {config_schema}"
            )

    data_schema = os.path.join(cli.schema, DATA_SCHEMA_FILE)
    if not os.path.exists(data_schema):
        return (
            False,
            f"Unable to find config schema. Expected: {data_schema}"
            )

    # validate config
    results = validate_config(config, config_schema)
    if not results[0]:
        return results

    # validate dataset
    results = validate_dataset(data, data_schema)
    if not results[0]:
        return results

    return (True, "Success")


def etl_step_2(config: dict, data: list[dict], release_path: str, cli):
    """
    process datasets - create parquet files, filter and merge columns
    """
    results = transform_datasets(data, release_path)

    if not results[0]:
        return (results[0], results[1])

    data = results[2]
    return (True, "Success")


def etl_step_3(config: dict, data: list[dict], release_path: str, cli):
    """
    Generate dataset columns - generate column data
    """
    results = build_datasets(data, config["special_columns"], release_path)
    if not results[0]:
        return (results[0], results[1])
    data = results[2]

    return (True, "Success")


def etl_step_4(config: dict, data: list[dict], release_path: str, cli):
    """
    Generate filters
    """
    results = generate_filters(data, config["filterCategories"], release_path)
    if not results[0]:
        return (results[0], results[1])
    data = results[2]

    return (True, "Success")


def etl_step_5(config: dict, data: list[dict], release_path: str, cli):
    """
    Generate duckdb
    """
    results = amr_release_to_duckdb(
        release_path,
        config["views"],
        cli.release
    )

    if not results[0]:
        return results

    return (True, "Success")


ETL_STEPS = [
    etl_step_1,
    etl_step_2,
    etl_step_3,
    etl_step_4,
    etl_step_5,
]


def create_release(release) -> str:
    release_path = release
    if not os.path.exists(release_path):
        os.mkdir(release_path)
    else:
        print("ERROR! Release already exists!")
        sys.exit(-1)
    return release_path


def load_json(path):
    with open(path, "r") as json_in:
        return json.load(json_in)


def run_etl():

    print("Loading configs")
    cli = get_cli_args().parse_args(sys.argv[1:])
    
    config = load_json(cli.config)
    data = load_json(cli.data)

    print("Creating release directory")
    release_path = create_release(cli.release)

    print("Running AMR ETL")

    for step in ETL_STEPS:
        print(f"ETL: {step.__name__}: {step.__doc__}")
        results = step(config, data, release_path, cli)
        if not results or not results[0]:
            print(f"ERROR! Step {step.__name__} failed")
            print(f"Reason: {results[1]}")
            sys.exit(-1)
    print("Success!")
