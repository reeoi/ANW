import sqlite3

conn = sqlite3.connect("D:\\Development_alma\\anp\\data\\anp.h3_test.sqlite3")
c = conn.execute("PRAGMA table_info(stories)")
print("columns:", [r[1] for r in c.fetchall()])
c = conn.execute("SELECT id, status, summary FROM stories")
for r in c.fetchall():
    print(f"id={r[0]}, status={r[1]}")
