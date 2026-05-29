import json

d = json.load(open("data/theme_pool.json", encoding="utf-8"))
print("item count:", len(d["items"]))
