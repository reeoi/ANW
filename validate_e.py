
# 字数统计
final_path = "D:\\Development_alma\\anw\\data\\works\\1\\5_最终稿.md"
text = open(final_path, encoding="utf-8").read()

# 中文字符数
chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
print("chars:", chinese_chars)

# 黑名单检查
blacklist = ["仿佛", "不禁", "不由得", "整个人都", "心底深处", "微微一笑", "一抹"]
hits = []
for word in blacklist:
    if word in text:
        hits.append(word)

print("blacklist_hits:", hits if hits else "none")

# 检查是否是 fallback
is_fallback = text.startswith("(fallback")
print("is_fallback:", is_fallback)
