import duckdb
import json
from pathlib import Path


class Dataset:
    """Create configuration for datasets for later use. To do this it

    - Combines special_columns configuration specified in the config.json file
    - Loops through all datasets
    - Loads parquet and queries for the column names
    - Set the various attributes including reformatting column names to something more readable
    - Saving to JSON in the release_path

    Attributes:
      - datasets: list[dict] of datasets to process
      - special_columns: dict[str,dict] of additional edits to apply to columns
      - release_path: where to write data to
    """

    def __init__(
        self, datasets: list[dict], special_columns: dict[str, dict], release_path: Path
    ):
        self.datasets = datasets
        self.special_columns = special_columns
        self.release_path = release_path

    def run(self) -> None:
        with duckdb.connect() as conn:
            for dataset in self.datasets:
                name = dataset["name"]
                parquet_path = dataset["parquet"]
                dataset_special_columns = self.special_columns.get(name, {})
                new_dataset = self.process_dataset(
                    name, parquet_path, dataset_special_columns, conn
                )
                dataset["column_meta"] = self.write_dataset(name, new_dataset)

    def write_dataset(self, name, dataset) -> Path:
        save_path = self.release_path / f"dataset-{name}.json"
        with open(save_path, "w") as fh:
            json.dump(dataset, fh, indent=4)
        return save_path

    def process_dataset(
        self,
        name: str,
        parquet_path: Path,
        dataset_special_columns: dict,
        conn: duckdb.DuckDBPyConnection,
    ) -> dict[str, any]:
        dataset = {"table": name, "columns": []}
        conn.read_parquet(str(parquet_path))
        results = self.get_columns(parquet_path, conn)
        for row in results:
            column_name, _ = row
            column_special_config = dataset_special_columns.get(column_name, {})
            # Skip if the field is mean to be hidden
            if "hidden" in column_special_config:
                continue
            # If not given a label we'll generate one
            label = column_special_config.get("label", "")
            if not label:
                label = column_name.replace("_", " ")
                label = label[0].upper() + label[1:]
            # Build definition
            column_definition = {
                "id": column_name,
                "label": label,
                "type": column_special_config.get("type", "string"),
                "sortable": True if column_special_config.get("sortable", 1) else False,
            }
            # Copy remaining keys
            for key, val in column_special_config.items():
                if key in ["type", "sortable", "label", "hidden"]:
                    continue
                column_definition[key] = val
            dataset["columns"].append(column_definition)
        return dataset

    def get_columns(self, parquet_path: Path, conn: duckdb.DuckDBPyConnection):
        sql = f"SELECT column_name, column_type FROM (describe'{str(parquet_path)}')"
        return conn.sql(sql).fetchall()
