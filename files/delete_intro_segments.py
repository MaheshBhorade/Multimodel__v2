import sqlite3
conn = sqlite3.connect('content_platform.db')
cur = conn.cursor()

# Find count of segments to delete
cur.execute('''
    SELECT COUNT(*) FROM content_segments cs
    JOIN content c ON c.content_id = cs.content_id
    WHERE c.series_name = "Goyamart" 
      AND c.content_id != "series-goyamart-intro"
      AND cs.segment_offset <= 50
''')
count = cur.fetchone()[0]
print(f"Found {count} segments to delete (offset <= 50 seconds) from Goyamart episodes.")

if count > 0:
    cur.execute('''
        DELETE FROM content_segments
        WHERE segment_id IN (
            SELECT cs.segment_id FROM content_segments cs
            JOIN content c ON c.content_id = cs.content_id
            WHERE c.series_name = "Goyamart" 
              AND c.content_id != "series-goyamart-intro"
              AND cs.segment_offset <= 50
        )
    ''')
    conn.commit()
    print("Deleted segments successfully.")
else:
    print("No segments found to delete.")

conn.close()
