from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from backend.api.endpoints import router as api_router
from backend.core.config import get_settings

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)

app.include_router(api_router, prefix="/api")

settings = get_settings()
if settings.enable_dataconnect:
    from backend.dataconnect.dataconnect import router as dataconnect_router
    app.include_router(dataconnect_router, prefix="/dataconnect")
