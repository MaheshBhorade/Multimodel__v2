import sys
import os
import shutil
from pathlib import Path

# Add project root and src subdirectory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from content_platform.server.db import SessionLocal, engine
from content_platform.server.models import Capture, RecognitionResultRecord, PlaybackSession


def clear_all_dashboard_data():
    print("WARNING: This will completely delete all capture logs, recognition results, playback sessions, and snapshot files.")
    confirm = input("Are you sure you want to proceed? (y/n): ").strip().lower()
    if confirm != 'y':
        print("Cancelled.")
        return
        
    print("\nStarting cleanup...")
    
    with SessionLocal() as db:
        try:
            # Delete recognition results
            res_count = db.query(RecognitionResultRecord).delete()
            print(f"Deleted {res_count} RecognitionResultRecord entries.")
            
            # Delete captures
            cap_count = db.query(Capture).delete()
            print(f"Deleted {cap_count} Capture entries.")
            
            # Delete playback sessions
            session_count = db.query(PlaybackSession).delete()
            print(f"Deleted {session_count} PlaybackSession entries.")
            
            db.commit()
            print("Database records cleared successfully.")
        except Exception as e:
            db.rollback()
            print(f"Failed to clear database records: {e}")
            return

    # Delete snapshot files on disk
    snapshot_dir = Path("runtime/snapshots")
    if snapshot_dir.exists():
        try:
            shutil.rmtree(snapshot_dir)
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            print("Successfully deleted all local snapshot files.")
        except Exception as e:
            print(f"Warning: Failed to clean snapshots directory: {e}")

    # Vacuum database to reclaim space
    print("Vacuuming SQLite database...")
    try:
        db_conn = engine.raw_connection()
        cursor = db_conn.cursor()
        cursor.execute("VACUUM")
        db_conn.commit()
        db_conn.close()
        print("Database vacuumed successfully.")
    except Exception as e:
        print(f"Failed to vacuum database: {e}")
        
    print("\nAll dashboard entries and capture data cleared successfully!")


if __name__ == "__main__":
    clear_all_dashboard_data()
