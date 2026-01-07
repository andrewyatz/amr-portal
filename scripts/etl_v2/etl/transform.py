from pathlib import Path
import duckdb
import logging

logger = logging.getLogger(__name__)


class Transform:
    """Performs first pass transformation. Takes an input parquet
    or CSV file and any additional columns wanted. Will then
    write the final files to the output path. Paths to parquet
    files are added to the given dataset variable.

    Key used is 'parquet'.

    Attributes:
        datasets (list[dict]): List of dataset definitions
        release_path (Path): Path to output release directory
        target (str): Name of the view to be created in DuckDB
    """

    def __init__(
        self, datasets: list[dict], release_path: Path, target: str = "dataset"
    ):
        self.datasets = datasets
        self.release_path = release_path
        self.target = target

    def run(self) -> None:
        for dataset in self.datasets:
            parquet_path = self.transform_dataset(dataset)
            dataset["parquet"] = parquet_path

    def transform_dataset(self, dataset: dict) -> Path:
        with duckdb.connect() as conn:
            # Set columns to all first
            dataset_cols = ["*"]

            # add additional columns as needed
            if "create_columns" in dataset:
                logger.info("Creating new columns")
                for column_definition in dataset["create_columns"]:
                    dataset_cols.append(
                        f"{column_definition['command']} AS {column_definition['name']}"
                    )

            if ".parquet" in dataset["path"]:
                sql_view = f"""
    CREATE VIEW {self.target} AS
    SELECT {", ".join(dataset_cols)}
    FROM read_parquet('{dataset['path']}')
            """
            elif ".csv" in dataset["path"]:
                sql_view = f"""
    CREATE VIEW {self.target} AS
    SELECT {", ".join(dataset_cols)}
    FROM read_csv('{dataset['path']}', header=true, all_varchar=true, delim =',', quote='"', sample_size = -1)
    """
            else:
                raise ValueError(
                    "Unsupported file format. Use .parquet or .csv (compressed csv should work)"
                )
            conn.execute(sql_view)
            return self.write_output(dataset, conn)

    def write_output(self, dataset: dict, conn) -> Path:
        # output as parquet
        name = f"{dataset['name']}.parquet"
        save_path = self.release_path / name
        output_sql = (
            f"COPY {self.target} TO '{save_path}' (FORMAT parquet, COMPRESSION zstd)"
        )
        conn.execute(output_sql)
        return save_path
