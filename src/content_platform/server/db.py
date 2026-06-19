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
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
        except Exception:
            pass
        finally:
            cursor.close()
else:
    connect_args = {"options": "-c timezone=utc"} if settings.database_url.startswith("postgresql") else {}
    engine = create_engine(
        settings.database_url,
        future=True,
        pool_size=20,
        max_overflow=50,
        connect_args=connect_args
    )

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


platform_connect_args = {"check_same_thread": False, "timeout": 30.0} if settings.platform_database_url.startswith("sqlite") else {}

if settings.platform_database_url.startswith("sqlite"):
    platform_poolclass = StaticPool if ":memory:" in settings.platform_database_url else NullPool
    platform_engine = create_engine(
        settings.platform_database_url,
        future=True,
        connect_args=platform_connect_args,
        poolclass=platform_poolclass
    )
    
    @event.listens_for(platform_engine, "connect")
    def set_platform_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
        except Exception:
            pass
        finally:
            cursor.close()
else:
    platform_connect_args = {"options": "-c timezone=utc"} if settings.platform_database_url.startswith("postgresql") else {}
    platform_engine = create_engine(
        settings.platform_database_url,
        future=True,
        pool_size=20,
        max_overflow=50,
        connect_args=platform_connect_args
    )

PlatformSessionLocal = sessionmaker(bind=platform_engine, autoflush=False, autocommit=False, future=True)
PlatformBase = declarative_base()


def get_platform_db() -> Generator[Session, None, None]:
    db = PlatformSessionLocal()
    try:
        yield db
    finally:
        db.close()