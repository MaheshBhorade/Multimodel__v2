import sqlite3
conn = sqlite3.connect('content_platform.db')
cur = conn.cursor()
res = cur.execute('SELECT r.id, c.captured_at, r.content_name, r.content_type, r.confidence, r.series, r.season, r.episode FROM recognition_results r JOIN captures c ON c.id = r.capture_id WHERE c.captured_at LIKE "%14:57:%" OR c.captured_at LIKE "%09:27:%" ORDER BY c.captured_at DESC').fetchall()
for r in res:
    print(r)
