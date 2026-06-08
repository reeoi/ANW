import json
import sqlite3

conn = sqlite3.connect("D:\\Development_alma\\anw\\data\\anw.h3_test.sqlite3")
c = conn.execute("SELECT id, title, status, summary, final_content_path FROM stories WHERE id=1")
r = c.fetchone()
print(f"id={r[0]}")
print(f"title={r[1]}")
print(f"status={r[2]}")
print(f"summary={r[3][:100] if r[3] else None}...")
print(f"final_path={r[4]}")

# 更新 plan slot 0 的 story_id
conn.execute("UPDATE daily_publish_plan SET slots_json = ? WHERE date = '2026-05-07'",
    (json.dumps([
        {"slot_time": "2026-05-07T15:08:00", "story_id": 1, "published_at": None, "skipped_reason": None},
        {"slot_time": "2026-05-07T15:43:00", "story_id": None, "published_at": None, "skipped_reason": None},
        {"slot_time": "2026-05-07T16:26:00", "story_id": None, "published_at": None, "skipped_reason": None}
    ]),))
conn.commit()
print("updated slot 0 story_id to 1")
