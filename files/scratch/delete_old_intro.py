import sqlite3
conn = sqlite3.connect('content_platform.db')
conn.execute("DELETE FROM content_segments WHERE content_id='series-18daac61'")
conn.execute("DELETE FROM content WHERE content_id='series-18daac61'")
conn.commit()
print("Deleted old intro entry and segments")
conn.close()
