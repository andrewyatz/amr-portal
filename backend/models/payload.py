from typing import List, Literal, Optional
from pydantic import BaseModel


class SelectedFilter(BaseModel):
    category: str
    value: str
    filter_type: Literal["exact", "like", "in", "location", "list_contains"] = "in"

    def trimmed_category(self) -> str:
        return self.category.split("-")[-1]


class OrderBy(BaseModel):
    category: str
    order: Literal["ASC", "DESC"]

    def trimmed_category(self) -> str:
        return self.category.split("-")[-1]


class Payload(BaseModel):
    selected_filters: List[SelectedFilter]
    view_id: int
    page: Optional[int] = 1
    per_page: Optional[int] = 100
    order_by: Optional[OrderBy] = None
