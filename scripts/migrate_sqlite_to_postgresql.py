import os
import sys
import json
from urllib.parse import urlparse
import psycopg
from sqlalchemy import create_engine, text, Text, String
from sqlalchemy.orm import sessionmaker

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from content_platform.shared.config import get_settings
from content_platform.server.db import Base, PlatformBase
from content_platform.server.models import (
    Device, Capture, Content, ContentSegment, RecognitionResultRecord, PlaybackSession, PlatformReference
)

# Standard SQLite paths
SQLITE_DB_URL = "sqlite:///./content_platform.db"
SQLITE_PLATFORM_DB_URL = "sqlite:///./platform_library.db"

def ensure_postgres_db_exists(url_str):
    if not url_str.startswith("postgresql"):
        print(f"Skipping database check (non-postgresql URL: {url_str})")
        return
    native_url = url_str.replace("+psycopg", "")
    parsed = urlparse(native_url)
    db_name = parsed.path.lstrip('/')
    
    # Construct URL for default 'postgres' database
    postgres_path = parsed._replace(path='/postgres')
    postgres_url = postgres_path.geturl()
    
    try:
        conn = psycopg.connect(native_url)
        conn.close()
        print(f"PostgreSQL Database '{db_name}' already exists.")
    except psycopg.OperationalError as e:
        if "does not exist" in str(e):
            print(f"Database '{db_name}' does not exist, creating...")
            try:
                conn = psycopg.connect(postgres_url, autocommit=True)
                with conn.cursor() as cur:
                    from psycopg import sql
                    cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
                conn.close()
                print(f"Database '{db_name}' created successfully.")
            except Exception as ex:
                print(f"Failed to create database '{db_name}': {ex}")
                raise ex
        else:
            raise e

def reset_identity_sequence(session, table_name, pk_column):
    try:
        seq_res = session.execute(text(f"SELECT pg_get_serial_sequence('{table_name}', '{pk_column}')")).scalar()
        if seq_res:
            max_res = session.execute(text(f"SELECT MAX({pk_column}) FROM {table_name}")).scalar()
            max_val = max_res if max_res is not None else 1
            session.execute(text(f"SELECT setval('{seq_res}', {max_val}, true)"))
            session.commit()
            print(f"Reset sequence '{seq_res}' to {max_val}.")
        else:
            print(f"No serial sequence found for {table_name}.{pk_column}")
    except Exception as e:
        session.rollback()
        print(f"Warning: Could not reset sequence for {table_name}.{pk_column}: {e}")

def migrate_table(sqlite_session, pg_session, model_class, chunk_size=1000):
    total_count = sqlite_session.query(model_class).count()
    print(f"Migrating {total_count} records for {model_class.__name__}...")
    
    # Find the primary key column to use for sorting
    pk_col = model_class.__table__.primary_key.columns[0]
    
    for offset in range(0, total_count, chunk_size):
        records = sqlite_session.query(model_class).order_by(pk_col).offset(offset).limit(chunk_size).all()
        for record in records:
            data = {}
            for col in model_class.__table__.columns:
                val = getattr(record, col.name)
                # Convert list/dict objects back to json strings if model expects strings
                if isinstance(col.type, (Text, String)) and isinstance(val, (list, dict)):
                    val = json.dumps(val)
                data[col.name] = val
            new_record = model_class(**data)
            pg_session.add(new_record)
        pg_session.commit()
    print(f"Finished migrating {model_class.__name__}.")

def main():
    settings = get_settings()
    
    pg_db_url = settings.database_url
    pg_platform_db_url = settings.platform_database_url
    
    print("PostgreSQL connection details:")
    print(f"Main DB URL: {pg_db_url}")
    print(f"Platform DB URL: {pg_platform_db_url}")
    
    if "sqlite" in pg_db_url or "sqlite" in pg_platform_db_url:
        print("ERROR: Target database URLs in environment/config are configured to use SQLite.")
        print("Please configure PostgreSQL connection settings in your .env file first.")
        print("Example:")
        print("CRP_DATABASE_URL=postgresql+psycopg://crp:crp@localhost:5432/content_platform")
        print("CRP_PLATFORM_DATABASE_URL=postgresql+psycopg://crp:crp@localhost:5432/platform_library")
        sys.exit(1)
        
    # 1. Ensure databases exist in PostgreSQL
    print("\nVerifying PostgreSQL databases exist...")
    ensure_postgres_db_exists(pg_db_url)
    ensure_postgres_db_exists(pg_platform_db_url)
    
    # 2. Setup engines and sessions
    print("\nConnecting to SQLite databases...")
    sqlite_engine = create_engine(SQLITE_DB_URL, future=True)
    sqlite_platform_engine = create_engine(SQLITE_PLATFORM_DB_URL, future=True)
    
    sqlite_Session = sessionmaker(bind=sqlite_engine, future=True)
    sqlite_platform_Session = sessionmaker(bind=sqlite_platform_engine, future=True)
    
    sqlite_session = sqlite_Session()
    sqlite_platform_session = sqlite_platform_Session()
    
    print("Connecting to PostgreSQL databases...")
    pg_engine = create_engine(pg_db_url, future=True, connect_args={"options": "-c timezone=utc"})
    pg_platform_engine = create_engine(pg_platform_db_url, future=True, connect_args={"options": "-c timezone=utc"})
    
    # Drop existing PostgreSQL tables to start fresh
    print("Dropping existing tables in PostgreSQL to start fresh...")
    try:
        Base.metadata.drop_all(bind=pg_engine)
        PlatformBase.metadata.drop_all(bind=pg_platform_engine)
    except Exception as de:
        print(f"Warning during drop_all: {de}")

    # Create PostgreSQL schemas
    print("Re-creating tables in PostgreSQL...")
    Base.metadata.create_all(bind=pg_engine)
    PlatformBase.metadata.create_all(bind=pg_platform_engine)
    
    pg_Session = sessionmaker(bind=pg_engine, future=True)
    pg_platform_Session = sessionmaker(bind=pg_platform_engine, future=True)
    
    pg_session = pg_Session()
    pg_platform_session = pg_platform_Session()
    
    try:
        # Migrate main database tables in topological/dependency order
        # Device has no dependencies
        migrate_table(sqlite_session, pg_session, Device)
        
        # Capture depends on Device
        migrate_table(sqlite_session, pg_session, Capture)
        
        # Content has no dependencies
        migrate_table(sqlite_session, pg_session, Content)
        
        # ContentSegment depends on Content
        migrate_table(sqlite_session, pg_session, ContentSegment)
        
        # RecognitionResultRecord depends on Capture
        migrate_table(sqlite_session, pg_session, RecognitionResultRecord)
        
        # PlaybackSession has no hard FK dependencies
        migrate_table(sqlite_session, pg_session, PlaybackSession)
        
        # Migrate platform database tables
        migrate_table(sqlite_platform_session, pg_platform_session, PlatformReference)
        
        # Reset primary key identity sequences
        print("\nResetting PostgreSQL primary key identity sequences...")
        reset_identity_sequence(pg_session, "devices", "id")
        reset_identity_sequence(pg_session, "captures", "id")
        reset_identity_sequence(pg_session, "content_segments", "segment_id")
        reset_identity_sequence(pg_session, "recognition_results", "id")
        reset_identity_sequence(pg_session, "playback_sessions", "id")
        reset_identity_sequence(pg_platform_session, "platform_reference", "id")
        
        print("\nDATABASE MIGRATION COMPLETED SUCCESSFULLY!")
        
    except Exception as e:
        print(f"\nMigration failed: {e}")
        pg_session.rollback()
        pg_platform_session.rollback()
        raise e
    finally:
        sqlite_session.close()
        sqlite_platform_session.close()
        pg_session.close()
        pg_platform_session.close()

if __name__ == "__main__":
    main()
