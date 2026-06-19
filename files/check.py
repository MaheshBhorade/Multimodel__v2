from content_platform.server.db import SessionLocal
from content_platform.server.models import ContentLibrary

db = SessionLocal()

print("TOTAL ROWS:", db.query(ContentLibrary).count())

titles = (
    db.query(ContentLibrary.title)
    .distinct()
    .all()
)

print("\nCONTENTS:")

for t in titles:
    print(t[0])