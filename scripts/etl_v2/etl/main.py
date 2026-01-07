import sys
from pathlib import Path
import json
import logging

logging.basicConfig(level=logging.INFO)

try:
    import yaml
except ImportError:
    yaml = None


from etl.cli import get_cli_args
from etl.config import validate_config, validate_dataset
from etl.transform import Transform
from etl.dataset import Dataset
from etl.filters import Filters
from etl.database import Database, DatabaseConfig

CONFIG_SCHEMA_FILE = "config.json"
DATA_SCHEMA_FILE = "dataset.json"


def create_release(release) -> Path:
    release_path = Path(release)
    if release_path.exists():
        raise ValueError(f"ERROR! Release {release_path!r} already exists!")
    release_path.mkdir(parents=True, exist_ok=False)
    return release_path


def load_data(path):
    with open(path, "r") as fh:
        if path.endswith(".yml") or path.endswith(".yaml"):
            if yaml is None:
                raise ImportError("PyYAML is required to load YAML files")
            return yaml.safe_load(fh)
        elif path.endswith(".json"):
            return json.load(fh)
        else:
            raise ValueError("Unsupported file format. Use .json or .yml/.yaml")


def validate_configs(config: dict, data: dict, schemas: str):
    """
    Validate config and dataset
    """
    config_schema = _load_schema(schemas, CONFIG_SCHEMA_FILE, "config")
    data_schema = _load_schema(schemas, DATA_SCHEMA_FILE, "data")
    # validate config
    validate_config(config, config_schema)
    # validate dataset
    validate_dataset(data, data_schema)


def _load_schema(schemas: str, schema_file_name: str, type: str) -> str:
    schema_file = Path(schemas) / schema_file_name
    if not schema_file.exists():
        return (False, f"Unable to find {type} schema. Expected: {schema_file}")
    with open(schema_file, "rt") as fh:
        return json.load(fh)


def run_etl():

    print("Loading configs")
    cli = get_cli_args().parse_args(sys.argv[1:])

    config = load_data(cli.config)
    data = load_data(cli.data)

    print("Stage 0: Creating release directory")
    release_path = create_release(cli.release)

    print("Stage 1: Validating ETL configurations")
    validate_configs(config, data, cli.schema)

    print("Stage 2: Running first pass ETL")
    Transform(datasets=data, release_path=release_path).run()

    print("Stage 4: Creating dataset configuration files")
    special_columns = config.get("special_columns", {})
    Dataset(
        datasets=data, release_path=release_path, special_columns=special_columns
    ).run()

    print("Stage 4: Preconfiguring filter values")
    Filters(
        datasets=data,
        filter_categories=config.get("filterCategories", {}),
        release_path=release_path,
    ).run()

    print("Stage 5: Creating final DuckDB configurations")
    with DatabaseConfig(release_path, cli.release, config.get("views")) as database:
        database.run()
    print("Stage 6: Copying data to DuckDB")
    with Database(release_path, cli.release) as database:
        database.run()

    print("Success!")
