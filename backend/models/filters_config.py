from pydantic import BaseModel, Field, ConfigDict
from typing import Any, Optional


class FilterOption(BaseModel):
    label: str
    value: str


class FilterCategory(BaseModel):
    # e.g. "phenotype-antibiotic_name"
    id: str
    label: str
    # e.g. "phenotype" | "genotype"
    dataset: str
    # Filter type from the new schema
    filter_type: str = "select_list"
    match_type: Optional[str] = None
    min: Optional[float] = None
    max: Optional[float] = None
    query_columns: Optional[Any] = None
    regex: Optional[str] = None
    # Only populated for select_list type
    filters: list[FilterOption] = []


class Column(BaseModel):
    # e.g. "phenotype-Antibiotic_name"
    id: str
    label: str
    sortable: bool
    rank: int
    enable_by_default: bool = Field(alias="enable_by_default")
    hidden: bool = False


class FilterGroup(BaseModel):
    id: str
    label: str
    rank: int
    # list of category ids (filter id strings), e.g. ["phenotype-antibiotic_name"]
    categories: list[str]


class FilterView(BaseModel):
    id: int
    url_name: str
    name: str
    # Single ranked list of filter groups (replaces categoryGroups/otherCategoryGroups)
    filterGroups: list[FilterGroup]
    columns: list[Column]


class FiltersConfig(BaseModel):
    model_config = ConfigDict(validate_by_name=True)

    filter_categories: dict[str, FilterCategory] = Field(alias="filterCategories")
    filter_views: list[FilterView] = Field(alias="filterViews")
    release: dict = Field()
