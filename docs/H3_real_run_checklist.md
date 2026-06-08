# Phase H.3 — 真实 1 篇生成验收 Checklist

> 最终交付前的人工验收单。Phase A-H 自动化测试已全部跑通(351 passed,c_pipeline 范围覆盖率 90%);本步骤需要真实 DEEPSEEK_API_KEY + 番茄登录态,**不能由 AI 代跑**。
>
> 走完整张单子,如果第 (g) 步人工 review 合格、第 (i) 步浏览器肉眼通过,则 c_pipeline 重构整个项目交付完成。

---

## 0. 决策映射(给自己回顾)

| 决策 | 锁定值 | 本次验证点 |
|---|---|---|
| #1 | C 全流程重构,8000-15000 字 | (e) 检查 5_最终稿.md 字数 |
| #2 | D1 纯 DeepSeek | (b) DEEPSEEK_API_KEY 已配置 |
| #16 | 节级 800 字 / 段长 ≤60 字 / AI 腔黑名单 0 命中 | (g) dashboard 看 phase 进度条 + 黑名单零命中 |
| #22/23/24 | 触顶降级 / 月预算 100 CNY / 800k tokens/天 | (g) dashboard 看 cost 卡片 |
| #29 | T2 阈值 90 | (f) ai_review --threshold 90 |
| #32 | K2 同时 ≤2 篇 | (g) dashboard 看 /api/monitor/concurrency |

---

## a. 备份 sqlite

```powershell
# 选项 A:备份现有 prod sqlite
Copy-Item "D:\Development_alma\anw\data\anw.sqlite3" `
          "D:\Development_alma\anw\data\anw.backup.before_h3.sqlite3"

# 选项 B(推荐):新建临时 sqlite,跑完后丢弃
$env:ANW_SQLITE_PATH = "D:\Development_alma\anw\data\anw.h3_test.sqlite3"
Remove-Item $env:ANW_SQLITE_PATH -ErrorAction Ignore
```

✅ 验收:`data/` 下能看到备份文件;或 `ANW_SQLITE_PATH` 指向不存在的临时路径(下一步会自动建表)。

---

## b. 配置真实 DEEPSEEK_API_KEY

```powershell
# 在 .env 里加入
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxx     # 真实 v4-pro 可用的 key
```

或临时:
```powershell
$env:DEEPSEEK_API_KEY = "sk-xxxxxxxxxxxx"
```

⚠️ **注意**:
- key 必须支持 v4-pro 模型(本次全程 pro,只在触顶时切 flash)
- key 余额预计需要 1-2 CNY(单篇估算见 PLAN §6,7-phase ≈ 0.65 CNY)
- 不要把 key 提交到 git;`.env` 已在 `.gitignore`

✅ 验收:`python -c "import os; print(bool(os.getenv('DEEPSEEK_API_KEY')))"` 输出 `True`。

---

## c. 跑 weekly_scan,产 100 题材

```powershell
cd D:\Development_alma\anw
python -m cli.scan_now --force
```

预期输出:
```
iso_week=2026Wxx
item_count=100
used_fallback=False
pool_path=D:\Development_alma\anw\data\theme_pool.json
weekly_topics=...(5 个)
```

✅ 验收清单:
- [ ] `data/theme_pool.json` 大小 > 30 KB
- [ ] `python -c "import json; d=json.load(open('data/theme_pool.json',encoding='utf-8')); print(len(d['items']))"` 输出 `100`
- [ ] `pipeline_cost_log` 出现一行 `phase='weekly_scan'`(可在 dashboard 看,或 `sqlite3 data/anw.sqlite3 "select phase, model, cost_cny from pipeline_cost_log order by id desc limit 5"`)
- [ ] 控制台不出现 `[mock]` 字样(说明走了真实 API)

如果失败:
- `BLOCKED: weekly_scan failed and no theme_pool.json fallback exists` — 检查 api_key 是否有效、网络是否能访问 api.deepseek.com
- `WeeklyScanBlockedError` 但有 used_fallback — 上周 pool 还在,可以接受;若想强制重跑加 `--force`

---

## d. 跑 cli/generate,全流程产稿

```powershell
python -m cli.generate --print-summary --print-ids
```

⏱️ 时长预期:**10-30 分钟**(Phase 4 精修最耗时,Phase 1/2 思考模式约 1-3 分钟)。

预期 stdout 末尾:
```
{story_id}                          ← --print-ids
Generated story_id={N} status=pending final_phase=phase_5_done chars={8000-15000} ...
--- final_title ---
... (Phase 1 LLM 取的标题)
--- summary ---
... (150-300 字简介)
--- final_content_path ---
D:\Development_alma\anw\data\works\{N}\5_最终稿.md
```

✅ 验收清单:
- [ ] `status=pending`(不是 needs_human / failed)
- [ ] `chars` 在 [8000, 15000] 区间
- [ ] `data/works/{story_id}/` 6 个产物文件齐全:
  - `0_选题.json`(theme/emotion/style/refs_from_pool)
  - `1_设定.md`(final_title + summary 头部 + 设定+反转)
  - `2_小节大纲.md`(Markdown 表格,8-15 行)
  - `3_正文_第 NN 节.md`(N 个,每个 ≥800 字)
  - `3_正文_合稿.md`(全节合并)
  - `4_精修稿.md`(整篇精修)
  - `5_最终稿.md`(去 AI 味,= final_content_path)
- [ ] `pipeline_cost_log` 出现 6+ 行(phase_0..phase_5,Phase 3 还会有 phase_3_aggregate 或多个 phase_3_section_NN)
- [ ] `stories.pipeline_cost_cny` ≥ 0.4(全 pro 估算)

如果失败:
- `Pipeline failed: pipeline failed at failed_at_phase_X` — work_dir 仍保留,可手工修中间产物后:`python -m cli.continue_pipeline --story-id N --resume-from phase_X`
- 字数 < 8000 — Phase 3 节级硬校验有 ≤2 次重试,失败后会标 needs_human;看 `data/works/N/` 各节文件检查具体哪一节字数不达标

---

## e. 字数 / 篇幅人工抽检

```powershell
$story_id = "{填入上一步的 story_id}"
$final = "data\works\$story_id\5_最终稿.md"

# 字数
(Get-Content $final -Raw).Length

# 也可以
python -c "from generator.c_pipeline.validators import count_chinese_chars; print(count_chinese_chars(open('$final',encoding='utf-8').read()))"
```

✅ 验收清单:
- [ ] 中文字符数(`count_chinese_chars`)在 [8000, 15000]
- [ ] 文件不是 Phase 3 mock 兜底文本(开头不出现 `(fallback 第 X 节)` 字样)
- [ ] 段落短:随意翻 5 段,每段 ≤60 字(若超出,Phase 5 去 AI 味本应处理)
- [ ] 没有命中 AI 腔黑名单常见词:`仿佛`、`不禁`、`不由得`、`整个人都`、`心底深处`、`微微一笑`、`一抹`(全文搜)

---

## f. AI 审核

```powershell
python -m cli.ai_review --threshold 90
```

预期输出:
```
AI 审核批次完成:审核 1 篇,通过 1 篇,转人工 0 篇,失败 0 篇。
reviewed=1
approved=1
needs_human=0
failed=0
```

✅ 验收清单:
- [ ] `approved=1`(真实 v4-pro 给 7 维评分,加权 ≥90)
- [ ] `pipeline_cost_log` 多一行 `phase='ai_review'`(若 mock=False 走 _live_review,会经 metrics.record_api_usage,部分场景不走 cost_log,以 dashboard /api/monitor 实际数据为准)
- [ ] `stories.status='approved'`、`stories.ai_review_score≥90`、`stories.ai_review_attempts=0`(一次过)

如果 `decision='rewrite'`:
- 自动 R2 重跑 Phase 4-5,最多 3 次,看 `ai_review_attempts`
- 还失败 → `status='needs_human'`,看 `stories.summary` 字段里 issues / suggestions

---

## g. 人工 review:打开 dashboard

```powershell
# 启动 web ui
python -m uvicorn review_queue.human_review:app --host 127.0.0.1 --port 8765
# 浏览器访问 http://127.0.0.1:8765
```

✅ 验收清单(逐项点开):
- [ ] **首页**列表能看到刚生成的故事,status=approved
- [ ] **进度条**(Phase F.1 / phase_progress)显示 6 个 phase 全绿,current_phase=phase_5_done
- [ ] **work_dir 文件浏览**(Phase F.1):点开 `1_设定.md` / `2_小节大纲.md` / `5_最终稿.md`,内容能正常渲染
- [ ] **简介**(`stories.summary`):150-300 字,可读
- [ ] **字数**:卡片显示 final 的中文字符数
- [ ] **AI 腔黑名单零命中**(Phase 5 deslop 后):`5_最终稿.md` 全文搜不到黑名单关键词
- [ ] **GET /api/monitor/concurrency**(Phase G.2):`{ok:true, max_concurrent:2, in_use:0, available:2}` (生成已结束,槽位空闲)
- [ ] **GET /api/monitor**:`limits.monthly_budget_cny=100`,`spent_30d_cny ≈ 你跑这一篇的真实花费`
- [ ] **续跑按钮**(可选,不点):仅确认 UI 上能看到入口

---

## h. 计划 + 草稿填充演练(不真发布)

```powershell
# 生成今天的发布计划(uniform[1,1] for 验收)
# 为了演练,临时把 daily_count_min/max 都设成 1 跑一下:
$env:ANW_PUBLISHER_DAILY_MIN = "1"
$env:ANW_PUBLISHER_DAILY_MAX = "1"
python -m cli.plan_today

# 看到 planned_count=1 后,演练 publish 草稿(--dry-run 不真发)
python -m cli.publish --dry-run --slot-id 0
```

预期:
- `cli.plan_today`:`date={today} planned_count=1 slot_count=1`
- `cli.publish --dry-run --slot-id 0`:
  - 输出 dry-run 摘要(标题、简介、字数)
  - **不会**点击番茄"发布"按钮
  - 浏览器(若 headless=False)弹出窗口仅做导航 + 字段填充,2-3 秒后关掉

✅ 验收清单:
- [ ] `daily_publish_plan.slots_json[0].story_id = {上一步的 story_id}`
- [ ] dry-run 输出包含 final_title、summary、字数;status=published(commit_dry_run 关闭时)或 status=skipped(commit_dry_run 关闭且只是预览)
- [ ] 不出现 `risk_control` / `paused` 字样

---

## i. **真正提交番茄**(用户手动按发布键)

⚠️ **本步骤不要让 AI 代跑**。打开 Playwright 浏览器肉眼看一遍。

```powershell
# 1. 关掉 headless,把浏览器窗口显示出来
$env:ANW_HEADLESS = "false"

# 2. 先 dry-run 走一遍(headless=false 状态下肉眼盯整个流程)
python -m cli.publish --dry-run --slot-id 0

# 3. 浏览器弹出后:
#    - 看登录态是否有效(URL=fanqienovel.com 已登录)
#    - 看标题字段是否填了 final_title
#    - 看正文字段是否填了 5_最终稿.md 全文
#    - 看简介字段是否填了 summary
#    - 看类目/封面默认值是否合理
#
# 4. 如果都对 → 关闭 dry-run 真实发布:
python -m cli.publish --slot-id 0
#    - 浏览器窗口里到了"发布"按钮前会停留(根据 fansq.py 的实现可能直接点;
#      若需手工确认,可在 publisher/fansq.py 里临时加 input("确认发布?") 后再点)
#
# 5. 发布完成后:
#    - stories.status='published'
#    - daily_publish_plan slot.published_at 写入
#    - 番茄后台能看到这一篇待审核 / 已发布
```

✅ 最终验收清单:
- [ ] 番茄后台能搜到这一篇(草稿 / 审核中 / 已发布,以番茄实际状态为准)
- [ ] 标题、简介、正文、字数与 dashboard 显示一致
- [ ] 没触发风控(没有滑块、没有人机验证、没有"操作过于频繁")
- [ ] `data/anw.sqlite3` 中 `stories.status='published'`、`pipeline_cost_log` 月度累计 < 100 CNY

---

## ✅ 通过判定

如果以上 a→i 全部打勾,**c_pipeline 重构整个项目交付完成**。

如果某一步失败:
- 失败在 a-c → 配置 / 网络问题,通常重跑就行
- 失败在 d → 检查 work_dir 各阶段产物,用 `cli.continue_pipeline` 续跑
- 失败在 f → AI 阈值过严,可降到 85 临时验收(或调 prompt)
- 失败在 g → UI 显示问题,Phase F endpoints 走的是 mock 逻辑,看真实 fansq cookies / browser state 是否过期
- 失败在 i → 番茄风控/登录态问题,**不是本次重构范围**,看 `publisher/fansq.py` 的 risk-control 截图(`logs/screenshots/`)

---

## 📞 联系点

跑完后用一句话回复 AI:

> H.3 全部通过 ✅ / 卡在 X 步,错误 = "..."

AI 收到后会:
- 通过 → 在 README 加一行"c_pipeline 已上线",归档 `docs/c_pipeline_plan.md` 到 `docs/archive/`
- 卡住 → 看错误信息再决定是补丁还是重新拷问对应分支

---

> 文档生成时间:2026-05-07
> 对应代码状态:HEAD = 53c018b refactor(c-pipeline): Phase H.2 — full e2e pipeline integration
> 上游链路:b2ae598 G.1 → 3e842e6 G.2 → 7640c5b G.3 → f7c3447 H.1 → 53c018b H.2
