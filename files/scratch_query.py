import sqlite3
conn = sqlite3.connect('content_platform.db')
cur = conn.cursor()
res = cur.execute('SELECT content_id, title, series_name, season_number, episode_number, content_type FROM content WHERE series_name = "Goyamart" OR title LIKE "%Goyamart%"').fetchall()
for r in res:
    print(r)
