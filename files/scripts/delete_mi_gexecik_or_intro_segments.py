import os
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from content_platform.shared.config import get_settings
from content_platform.server.models import ContentSegment, Content

def main():
    settings = get_settings()
    db_url = settings.database_url
    print(f"Connecting to database: {db_url}")
    
    connect_args = {"options": "-c timezone=utc"} if db_url.startswith("postgresql") else {}
    engine = create_engine(db_url, future=True, connect_args=connect_args)
    Session = sessionmaker(bind=engine, future=True)
    db = Session()
    
    try:
        # Find count of segments to delete
        query = (
            db.query(ContentSegment)
            .join(Content)
            .filter(Content.title.like("MI GEXECIK OR%"))
            .filter(Content.content_id != "series-mi_gexecik_or-intro")
            .filter(ContentSegment.segment_offset <= 50)
        )
        segments = query.all()
        count = len(segments)
        print(f"Found {count} duplicate intro segments to delete (offset <= 50s) from MI GEXECIK OR episodes.")
        
        if count > 0:
            for seg in segments:
                db.delete(seg)
            db.commit()
            print("Successfully deleted duplicate intro segments from database.")
        else:
            print("No duplicate intro segments found to delete.")
            
    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        db.close()

if __name__ == "__main__":
    main()
