import json
from pathlib import Path
import duckdb
import logging

logger = logging.getLogger(__name__)


class Filters:
    """Creates the filters available by querying distinct values from each dataset and 
    parquet file. Saves to JSON files in the release_path.
    
    Filters are based on the filterCategories defined in the config file. We also apply
    the filter_label text in the SQL select to get the label for each filter value. You can
    configure this to be different from the ID if needed e.g. lowercasing, trimming, adding prefixes etc.
    
    Attributes:
        - datasets: list[dict] of datasets to process
        - filter_categories: dict[str,dict] of filter categories to create
        - release_path: Path where to write data to
        - warn_max: int, maximum number of distinct filter values before warning. Default 60
    """

    def __init__(
        self,
        datasets: list,
        filter_categories: dict[str, dict],
        release_path: Path,
        warn_max: int = 60,
    ):
        self.datasets = datasets
        self.filter_categories = filter_categories
        self.release_path = release_path
        self.warn_max = warn_max

    def run(self):
        with duckdb.connect() as conn:
            for dataset in self.datasets:
                dataset_name = dataset["name"]
                filter_categories = self.filter_categories_by_dataset_name(dataset_name)
                filters = []
                for fc in filter_categories:
                    filter = dict(fc)
                    filter["title"] = filter["label"]
                    del filter["label"]
                    filter_values = self.distinct_filter_values(
                        fc, dataset["parquet"], conn
                    )
                    filter["filters"] = filter_values
                    size = len(filter_values)
                    if size == 0:
                        logging.warning(f"Problem. No values found for {fc["id"]!r}")
                    elif size > self.warn_max:
                        logging.warning(
                            f"{dataset_name} - {fc['id']!r} has over {self.warn_max} ({size}) values"
                        )
                    del filter["filter_label"]
                    filters.append(filter)
                self.write_dataset(dataset_name, filters)
        pass

    def distinct_filter_values(
        self, fc: dict, parquet: Path, conn: duckdb.DuckDBPyConnection
    ):
        distinct_sql = f"""
SELECT DISTINCT {fc["id"]} AS value, {fc["filter_label"]} AS label
from '{parquet}'
WHERE {fc["id"]} IS NOT NULL
ORDER BY label ASC
"""
        results = conn.sql(distinct_sql)
        columns = results.columns
        fetch_results = results.fetchall()
        filter_values = []
        for r in fetch_results:
            filter_values.append({columns[0]: str(r[0]), columns[1]: str(r[1])})
        return filter_values

    def filter_categories_by_dataset_name(self, dataset_name: str):
        return [fc for fc in self.filter_categories if fc["dataset"] == dataset_name]

    def write_dataset(self, name, filters) -> Path:
        save_path = self.release_path / f"filters-{name}.json"
        with open(save_path, "w") as fh:
            json.dump(filters, fh, indent=2)
        return save_path
