import sqlite3

conn = sqlite3.connect("D:\\Development_alma\\anw\\data\\anw.h3_test.sqlite3")
c = conn.execute("SELECT date, slots_json FROM daily_publish_plan ORDER BY date DESC LIMIT 1")
r = c.fetchone()
if r:
    print(f"date={r[0]}")
    print(f"slots={r[1][:500]}")
else:
    print("no plan found")
