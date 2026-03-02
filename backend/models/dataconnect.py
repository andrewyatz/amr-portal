from pydantic import BaseModel


class DataConnectQuery(BaseModel):
    query: str
    parameters: list = []
