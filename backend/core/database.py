from backend.core.config import get_settings
import duckdb

settings = get_settings()

def get_db_connection():
    """
    FastAPI dependency for database connections.
    Creates a new connection for each request.
    """
    conn = duckdb.connect(settings.duckdb_path, read_only=True)
    conn.execute("PRAGMA threads = 4")
    conn.execute("PRAGMA memory_limit = '2GB'")
    if settings.enable_dataconnect:
        conn.execute("SET enable_external_access = false")
        conn.execute("SET autoload_known_extensions = false")
        conn.execute("SET autoinstall_known_extensions = false")
        conn.execute("SET allow_community_extensions = false")
    conn.execute("SET lock_configuration = true")
    try:
        yield conn
    finally:
        conn.close()