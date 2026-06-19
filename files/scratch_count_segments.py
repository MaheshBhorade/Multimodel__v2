import sqlite3
conn = sqlite3.connect('content_platform.db')
cur = conn.cursor()
# Find count of content segments for Goyamart episodes where offset <= 50
res = cur.execute('''
    SELECT COUNT(*) FROM content_segments cs
    JOIN content c ON c.content_id = cs.content_id
    WHERE c.series_name = "Goyamart" 
      AND c.content_id != "series-goyamart-intro"
      AND cs.offset_seconds <= 50.0
''').fetchone()
print("Number of segments to delete:", res[0])
