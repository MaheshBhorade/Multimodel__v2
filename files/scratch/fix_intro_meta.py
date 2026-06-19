import sqlite3
conn = sqlite3.connect('content_platform.db')
conn.execute("UPDATE content SET series_name='Goyamart', season_number=NULL, episode_number=NULL WHERE content_id='series-18daac61'")
conn.commit()
row = conn.execute("SELECT content_id, title, platform_id, series_name, season_number, episode_number FROM content WHERE content_id='series-18daac61'").fetchone()
print("Verified:", row)
conn.close()
