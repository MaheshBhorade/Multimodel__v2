import sqlite3
conn = sqlite3.connect('content_platform.db')
cur = conn.cursor()
cur.execute("PRAGMA table_info(content_segments)")
print(cur.fetchall())
