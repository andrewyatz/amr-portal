from pathlib import Path
import logging
import duckdb
from pydantic import BaseModel, TypeAdapter

logger = logging.getLogger(__name__)


class DatasetColumnModel(BaseModel):
    id: str
    label: str
    type: str
    sortable: bool
    url: str = None
    delimiter: str = None
    db_id: None | int = None


class DatasetModel(BaseModel):
    columns: list[DatasetColumnModel]
    table: str
    db_id: None | int = None


class FilterValueModel(BaseModel):
    value: str
    label: str


class FiltersModel(BaseModel):
    dataset: str
    id: str
    title: str
    filters: list[FilterValueModel]

    @property
    def fullname(self):
        return f"{self.dataset}-{self.id}"


class ViewColumnModel(BaseModel):
    name: str
    enabled: bool = True
    rank: int


class CategoryGroupModel(BaseModel):
    categories: list[str]
    name: str
    is_primary: bool = False
    db_id: None | int = None


class ViewModel(BaseModel):
    dataset: str
    name: str
    url_name: str
    other_columns: list[str]
    categoryGroups: list[CategoryGroupModel]
    otherCategoryGroups: list[CategoryGroupModel]
    columns: list[ViewColumnModel]
    db_id: None | int = None

    @property
    def category_groups(self):
        self.categoryGroups[0].is_primary = True
        return self.categoryGroups + self.otherCategoryGroups


class BaseDatabase:
    def __init__(self, release_path: Path, release: str):
        self.release_path = release_path
        self.release = release

    def __enter__(self):
        path = self.release_path / f"{self.release}.duckdb"
        self.conn = duckdb.connect(str(path))
        return self

    def __exit__(self, type, value, traceback) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()


class DatabaseConfig(BaseDatabase):
    """Takes the configuration files now present in the release directory
    and loads them into the DuckDB database schema.

    Attributes:
        release_path (Path): Path to output release directory
        release (str): Release name
        views (dict): View configurations
        schema_version (str): Version of the database schema to use (default: v1)

    Files queried and processed are:
        - dataset-*.json
        - filters-*.json
        - views from the views attribute (see view config for the data structure)

    All data are processed using Pydantic models for validation and navigation. The code
    will create the schema on initialisation. We process

    - datasets first loading dataset and column_definition tables (and linker dataset_column)
    - filters second loading filter table
    - views third loading view, view_column, category_group, category, and linker category_group_category tables
    - lastly we create a release entry in the release table set to the current date in YYYY-MM format

    When loading the views we populate view columns which are in the view config first, then go back to the
    dataset's list of columns and and any remaining columns incrementing the rank and setting enable_by_default to False.
    """

    def run(self) -> None:
        self.load_schema()
        for ds in self.get_files("dataset"):
            self.process_dataset(ds)
        for f in self.get_files("filters"):
            self.process_filter(f)
        for v in TypeAdapter(list[ViewModel]).validate_python(self.views):
            self.process_view(v)
        self.generate_release()

    def process_dataset(self, content: str) -> None:
        conn = self.conn
        ds = DatasetModel.model_validate_json(content)
        ds.db_id = self.next_id("dataset")
        table = ds.table
        self.dataset_lookup[table] = ds
        conn.execute(
            "INSERT INTO dataset (dataset_id, name) VALUES (?,?)", (ds.db_id, table)
        )
        linker_ids = []
        for col in ds.columns:
            col.db_id = self.next_id("column_definition")
            fullname = f"{table}-{col.id}"
            sql = "INSERT INTO column_definition (column_id, fullname, name, label, type, sortable, url, delimiter) VALUES (?,?,?,?,?,?,?,?)"
            params = (
                col.db_id,
                fullname,
                col.id,
                col.label,
                col.type,
                col.sortable,
                col.url,
                col.delimiter,
            )
            conn.execute(sql, params)
            self.column_lookup[fullname] = col
            linker_ids.append([ds.db_id, col.db_id])
        conn.executemany(
            "INSERT INTO dataset_column (dataset_id, column_id) VALUES (?,?)",
            linker_ids,
        )

    def process_filter(self, content: str) -> None:
        conn = self.conn
        filters = TypeAdapter(list[FiltersModel]).validate_json(content)
        for filter in filters:
            column = self.column_lookup.get(filter.fullname)
            for f in filter.filters:
                sql = "INSERT INTO filter (column_id, value, label) VALUES (?,?,?)"
                params = (column.db_id, f.value, f.label)
                conn.execute(sql, params)

    def process_view(self, v: ViewModel):
        conn = self.conn
        v.db_id = self.next_id("view")
        logging.debug(f"Inserting view {v.name} with ID {v.db_id}")
        conn.execute(
            "INSERT INTO view (view_id, url_name, name) VALUES (?,?,?)",
            (v.db_id, v.url_name, v.name),
        )
        dataset = self.dataset_lookup.get(v.dataset)

        processed_columns = set()
        view_column_sql = "INSERT INTO view_column (view_id, column_id, rank, enable_by_default) VALUES (?,?,?,?)"
        for view_column in v.columns:
            fullname = f"{v.dataset}-{view_column.name}"
            column = self.column_lookup.get(fullname)
            params = (v.db_id, column.db_id, view_column.rank, view_column.enabled)
            conn.execute(view_column_sql, params)
            processed_columns.add(view_column.name)

        if v.other_columns:
            dataset_name = v.other_columns[0]
            if dataset_name != v.dataset:
                logging.warning(
                    f"View {v.name} has other_columns dataset {dataset_name} which does not match view dataset {v.dataset}"
                )
                raise ValueError(
                    f"Mismatched dataset names in view other_columns. Expected {v.dataset}, got {dataset_name}"
                )
            highest_rank = max(v.columns, key=lambda x: x.rank).rank
            for data_column in dataset.columns:
                if data_column.id in processed_columns:
                    continue
                highest_rank += 1
                params = (v.db_id, data_column.db_id, highest_rank, False)
                conn.execute(view_column_sql, params)

        for category_group in v.category_groups:
            category_group.db_id = self.next_id("category_group")
            logging.debug(
                f"Inserting view category_group {category_group.name} with ID {category_group.db_id}"
            )
            conn.execute(
                "INSERT INTO category_group (category_group_id, name, is_primary, view_id) VALUES (?,?,?,?)",
                (
                    category_group.db_id,
                    category_group.name,
                    category_group.is_primary,
                    v.db_id,
                ),
            )
            sql = "INSERT INTO category (category_id, dataset_id, column_id, title, name) VALUES (?,?,?,?,?)"
            linker_ids = []
            for category in category_group.categories:
                fullname = f"{v.dataset}-{category}"
                column = self.column_lookup.get(fullname)
                category_id = self.next_id("category")
                logging.debug(f"Inserting category {fullname} with ID {category_id}")
                params = (
                    category_id,
                    dataset.db_id,
                    column.db_id,
                    category_group.name,
                    fullname,
                )
                conn.execute(sql, params)
                linker_ids.append([category_group.db_id, category_id])
            conn.executemany(
                "INSERT INTO category_group_category (category_group_id, category_id) VALUES (?,?)",
                linker_ids,
            )

    def generate_release(self) -> None:
        sql = "INSERT INTO release (release_label) VALUES (strftime(current_date(),'%Y-%m'))"
        self.conn.execute(sql)

    def __init__(
        self, release_path: Path, release: str, views: dict, schema_version: str = "v1"
    ):
        BaseDatabase.__init__(self, release_path, release)
        self.views = views
        self.schema_version = schema_version
        self.dataset_lookup = {}
        self.column_lookup = {}
        self.ids = {}

    def load_schema(self):
        conn = self.conn
        path = (
            Path(__file__).parent.parent / "sql" / f"schema.{self.schema_version}.sql"
        )
        with open(path, "rt") as fh:
            content = fh.read()
            conn.execute(content)
        logging.info(f"Database schema loaded from {path!r}")

    def get_files(self, prefix: str):
        """Take a prefix, find all files in the release_path directory and load JSON content for further processing"""
        loaded = []
        for file in self.release_path.iterdir():
            if (
                file.is_file()
                and file.name.startswith(f"{prefix}-")
                and file.name.endswith(".json")
            ):
                with open(file, "rt") as fh:
                    content = fh.read()
                    loaded.append(content)
        return loaded

    def next_id(self, table) -> int:
        id = self.ids.get(table, 1)
        self.ids[table] = id + 1
        return id


class Database(BaseDatabase):
    """Copies the parquet files present in the release directory into the DuckDB
    database. Looks for all .parquet files in the release_path and creates a table
    for each file named after the file stem.
    """

    def run(self) -> None:
        for path in self.get_parquet_paths():
            self.load_parquet(path)

    def load_parquet(self, path: Path) -> None:
        table_name = path.stem
        logging.info(f"Loading parquet {path!r} into table {table_name!r}")
        self.conn.execute(
            f"CREATE TABLE {table_name} AS SELECT * FROM read_parquet('{path}')"
        )

    def get_parquet_paths(self):
        for file in self.release_path.iterdir():
            if file.is_file() and file.name.endswith(".parquet"):
                yield file
