import os

from jsonschema import validate
from jsonschema.exceptions import ValidationError


class ConfigError(Exception):
    pass


def validate_config(config: dict, schema: dict) -> None:
    # Validate JSON
    validate(instance=config, schema=schema)
    filter_ids = [f["id"] for f in config["filterCategories"]]
    used_filters = {f: False for f in filter_ids}

    # Check filter names
    for view in config["views"]:
        for cat_grp in view["categoryGroups"]:
            for cat in cat_grp["categories"]:
                if cat not in filter_ids:
                    raise ConfigError(
                        f"Unknown Category: {cat} found for {view['name']}"
                    )
                else:
                    used_filters[cat] = True
        for other in view["otherCategoryGroups"]:
            for cat in other["categories"]:
                if cat not in filter_ids:
                    raise ConfigError(
                        f"Unknown Category: {cat} found for {view['name']}"
                    )
                else:
                    used_filters[cat] = True
    # check for unused filter
    for f, used in used_filters.items():
        if not used:
            raise ConfigError(f"Category {f} is not used by any of the views!")


def validate_dataset(datasets: dict, schema: dict) -> None:
    validate(instance=datasets, schema=schema)
    for dataset in datasets:
        if not os.path.exists(dataset["path"]):
            return ConfigError(f"Unable to access {dataset['path']}")
