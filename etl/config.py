"""Settings for the ETL, read from the .env file in the project root."""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:  # python-dotenv not installed: fall back to real env vars
    pass

DATA_DIR = Path(os.getenv("DATA_DIR", PROJECT_ROOT / "data" / "raw"))
ARCHIVE_DIR = Path(os.getenv("ARCHIVE_DIR", PROJECT_ROOT / "data" / "archive"))

DB_SERVER = os.getenv("DB_SERVER", "localhost")
DB_PORT = os.getenv("DB_PORT", "")
DB_NAME = os.getenv("DB_NAME", "SalesPulseDB")
DB_USER = os.getenv("DB_USER", "")          # empty = Windows Authentication
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_DRIVER = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server")


def get_engine():
    """Create a SQLAlchemy engine for SQL Server.

    Imported lazily so `--dry-run` works on machines without SQLAlchemy/pyodbc.
    fast_executemany makes pandas.to_sql roughly 10-50x faster on SQL Server.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL

    query = {"driver": DB_DRIVER, "TrustServerCertificate": "yes"}
    if not DB_USER:
        query["Trusted_Connection"] = "yes"

    url = URL.create(
        "mssql+pyodbc",
        username=DB_USER or None,
        password=DB_PASSWORD or None,
        host=DB_SERVER,
        port=int(DB_PORT) if DB_PORT else None,
        database=DB_NAME,
        query=query,
    )
    return create_engine(url, fast_executemany=True)
