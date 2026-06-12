# from collections.abc import Generator

# from sqlalchemy import create_engine
# from sqlalchemy.orm import Session, declarative_base, sessionmaker

# from content_platform.shared.config import get_settings

# settings = get_settings()

# connect_args = {"check_same_thread": False, "timeout": 30.0} if settings.database_url.startswith("sqlite") else {}
# engine = create_engine(settings.database_url, future=True, connect_args=connect_args)

# from sqlalchemy import event
# @event.listens_for(engine, "connect")
# def set_sqlite_pragma(dbapi_connection, connection_record):
#     if settings.database_url.startswith("sqlite"):
#         cursor = dbapi_connection.cursor()
#         try:
#             cursor.execute("PRAGMA journal_mode=WAL")
#             cursor.execute("PRAGMA synchronous=NORMAL")
#         except Exception:
#             pass
#         finally:
#             cursor.close()

# SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
# Base = declarative_base()


# def get_db() -> Generator[Session, None, None]:
#     db = SessionLocal()
#     try:
#         yield db
#     finally:
#         db.close()
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from content_platform.shared.config import get_settings

settings = get_settings()

from sqlalchemy.pool import NullPool, StaticPool

connect_args = {"check_same_thread": False, "timeout": 30.0} if settings.database_url.startswith("sqlite") else {}

if settings.database_url.startswith("sqlite"):
    poolclass = StaticPool if ":memory:" in settings.database_url else NullPool
    engine = create_engine(
        settings.database_url,
        future=True,
        connect_args=connect_args,
        poolclass=poolclass
    )
    
    from sqlalchemy import event
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            pass
        finally:
            cursor.close()
else:
    engine = create_engine(
        settings.database_url,
        future=True,
        pool_size=20,
        max_overflow=50
    )

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()