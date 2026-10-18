"""PostgreSQL business storage. Runtime journals remain in Agent Runtime."""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


url = settings.database_url
if url.startswith("postgres://"):
    url = "postgresql+psycopg://" + url[len("postgres://") :]
elif url.startswith("postgresql://"):
    url = "postgresql+psycopg://" + url[len("postgresql://") :]
if not url.startswith("postgresql+psycopg://"):
    raise ValueError("DATABASE_URL must be a PostgreSQL connection URL")
engine = create_engine(url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
