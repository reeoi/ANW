# ANP 全可视化改造 · 完整实施计划

> **本文件目的**：把 ANP 项目从"必须改 YAML、跑命令行"改造成"全部 Web UI 操作 + 后台常驻 + 开机自启"的全可视化软件，**适合非技术用户使用**。
>
> **使用方法**：在 ANP 项目根目录（`D:\Development_alma\anp`）开启新对话，让 Claude 阅读本文件后**严格按 3 期顺序**执行。每一期都有独立验收标准，**做完一期再开下一期**。

---

## 0. 项目背景速览（新会话必读）

ANP 是一个**本地优先**的小说自动创作 + 发布流水线，技术栈：

- **语言**：Python 3.11+
- **Web 框架**：FastAPI（已有 5 个 tab 的管理界面，监听 `127.0.0.1:18000`）
- **调度**：APScheduler（cron 触发生成 → AI 审核 → 随机延迟发布 → SQLite 备份）
- **存储**：SQLite（`data/anp.sqlite3`）
- **AI**：DeepSeek API（已有 mock/dry-run 兜底）
- **发布**：Playwright 自动化（番茄小说 / fanqienovel.com）
- **运行模式**：`auto`（全自动）/`semi-auto`（半自动）+ `dry_run`（演练）

### 现有目录结构

```
D:\Development_alma\anp\
├── main.py                  # 统一入口，--mode auto / semi-auto
├── scheduler.py             # APScheduler 调度器，4 个 cron 任务
├── config_loader.py         # 读 config.yaml + 环境变量覆盖
├── config.yaml              # 默认配置（模板）
├── .env                     # 敏感字段（DEEPSEEK_API_KEY 等）
├── start_anp.bat            # Windows 一键启动脚本
├── start_anp.ps1            # PowerShell 启动
├── requirements.txt
├── generator/               # 生成模块（DeepSeek 客户端、prompt 构建）
├── publisher/               # 发布模块（番茄 Playwright 适配器）
├── review_queue/            # 队列 + Web UI
│   ├── human_review.py      # FastAPI 路由 + API（680 行）
│   ├── dashboard_assets.py  # CSS + HTML 模板 + JS（1150 行）
│   ├── ai_review.py         # AI 七维审核
│   ├── db.py / models.py    # SQLite ORM
│   └── metrics.py           # 队列统计
├── cli/                     # CLI 入口（保留，作为脚本/CI 用）
├── data/                    # SQLite + 浏览器登录态
├── logs/                    # 日志 + 截图
└── docs/                    # 文档（本文件位置）
```

### 现有 Web UI 状态

| Tab | 功能 | 状态 |
|---|---|---|
| 总览 Dashboard | 队列计数、最近作品、系统提醒 | ✅ 已有 |
| 监控面板 | 简单监控数据 | ✅ 已有 |
| 生成作品 | 单篇/批量生成表单 | ✅ 已有 |
| 审核队列 | 列表、批准/拒绝/编辑 | ✅ 已有 |
| 发布管理 | dry-run 触发 | ✅ 已有 |
| 日志查看 | 最近日志行 | ✅ 已有 |
| **配置说明** | **❌ 死文字，不可编辑** | **要改** |

### 当前必须改 YAML / 跑命令行的事

| 操作 | 现状 | 目标 |
|---|---|---|
| 改 DeepSeek API key | 编辑 `.env` | UI 输入框 + 保存 |
| 改 cron 时间 | 编辑 `config.yaml` | UI 时间选择器 |
| 切 dry_run / 真实模式 | 编辑 `config.yaml` | UI 顶部横幅切换 |
| 改 7 维度审核权重 | 编辑 `config.yaml` | UI 滑块 + 饼图 |
| 番茄登录 | 命令行 `playwright codegen` | UI 按钮弹 Chromium |
| 启 auto 模式 | 命令行 `python main.py --mode auto` | UI 总开关 |
| 开机自启 | 手动配 Task Scheduler/NSSM | UI 单一开关 |
| 后台常驻 | 关黑窗口=死 | 系统托盘图标 |

---

## 1. 关键决策记录（共 12 条，全部已敲定）

> 这些是"为什么这么做"的依据，每一条都和用户面对面讨论过。修改方案前必须先理解决策背景。

| # | 主题 | 选择 | 关键考虑 |
|---|---|---|---|
| 1 | **用户范围** | 仅本人单机使用 | 不做多用户/账号/HTTPS/exe 打包 |
| 2 | **启动方式** | **系统托盘图标** | 加 `pystray` + `Pillow`，常驻后台，4 色图标（绿/黄/红/灰）反映状态 |
| 3 | **设置位置** | **新增 ⚙️ 设置 tab** | 不动现有 5 tab，新加一个；6 大类折叠 |
| 4 | **番茄登录** | **弹真实 Chromium 手动登** | 合规（README §8 禁止绕风控）；用 `playwright sync_api launch(headless=False)` |
| 5 | **Cron 调度** | **4 个独立时间选择器 + 表达式预览** | 频率（每天/工作日/每周特定日/每小时） + 时间 picker；下方灰字显示生成的 cron |
| 6 | **开机自启** | **单开关，写 `shell:startup` 文件夹** | 不要管理员；放 `.bat` 快捷方式；取消勾选=删快捷方式 |
| 7 | **凭据显示** | **头尾露 6+4 字符** `sk-87b...1561` + 编辑时眼睛切换 | GitHub/AWS 风格；新输入支持临时显示；失焦后自动隐藏 |
| 8 | **通知通道** | **UI toast + 托盘色 + Windows 桌面气泡** | 不接外部（Telegram/邮件是后续范围） |
| 9 | **高级选项** | **大类折叠 + 节内"高级"二级折叠** | 80% 时间只看 8-10 个控件；每节有【💾 保存本节】+【↩️ 恢复默认】 |
| 10 | **Dashboard** | **加 4 张状态卡片** | ⏰ 下次运行倒计时 / ✅ 最近结果 / 🔐 登录态 / 💰 本月预算；5 秒轮询 |
| 11 | **模式切换** | **顶部全局横幅 + 颜色编码 + 二次确认** | 蓝=演练，橙=真实；切到真实需勾选 2 个确认项；切回演练直接切 |
| 12 | **实施节奏** | **分 3 期，每期独立可用** | 第 1 期：消灭改 YAML；第 2 期：托盘+自启；第 3 期：Dashboard 卡片+打磨 |

---

## 2. 新增依赖一览

需要追加到 `requirements.txt`：

```txt
# 第 1 期
ruamel.yaml>=0.17                # 保留注释/顺序的 YAML 读写

# 第 2 期
pystray>=0.19                    # Windows 系统托盘图标
Pillow>=10.0                     # 托盘图标位图（pystray 依赖）
pywin32>=306                     # Windows 通知 + shell:startup 操作
```

**第 3 期不引入新依赖**，纯 Python 标准库 + 现有依赖。

> 安装命令（验收时跑）：`.\.venv\Scripts\activate && pip install -r requirements.txt`

---

## 3. 全局设计原则与边界（新会话必看，不能违反）

### ✅ 必须保留 / 不能破坏的

1. **CLI 入口全部保留**：`python -m cli.generate` / `cli.batch_generate` / `cli.ai_review` / `cli.publish` 等，给 CI/脚本/调试用
2. **`python main.py --mode auto` 仍可独立运行**，不依赖托盘
3. **现有 5 个 tab 视觉风格不动**，只**新增**⚙️ 设置 tab
4. **现有 pytest 测试全部继续通过**（不改 `tests/` 旧测试，新功能写新测试）
5. **`config.yaml` 注释和顺序保留**（用 `ruamel.yaml`，不能用 `pyyaml` 重写）
6. **`.env` 注释和顺序保留**（手写解析，不用 `python-dotenv` 写回）
7. **不存番茄账号密码**（决策 4 已定，登录态由用户在弹出的浏览器手动输）
8. **README §8 风控原则**：禁止打码/模拟滑块/自动登录绕过

### ✅ 安全/合规要求

- 任何 API key、登录态**永不出现在日志、HTTP 响应、UI 明文显示**（仅头尾遮罩）
- `.env` 写入时 **chmod 0600**（Windows 上等价于限制 ACL，至少加密存储或限制权限）
- `data/browser/fansq_state.json` 不能上传/导出/通过 API 暴露
- UI 仅监听 `127.0.0.1`，不允许开放到 0.0.0.0
- 所有写文件操作**先写 `.tmp` 再原子 rename**，避免半途崩溃损坏配置

### ✅ 通用质量门槛

- 每个新模块单测覆盖率 ≥ 80%
- 文件 ≤ 400 行（超出拆分）
- 所有公开函数有 docstring
- 错误信息中文化（面向小白用户）
- 不引入 ORM 框架；继续手写 SQL（项目现有风格）
- 前端不引入 React/Vue；继续手写原生 JS（项目现有风格）

---

# 🥇 第 1 期：消灭改 YAML（核心痛点先解决）

**预估工作量**：1500-2000 行新代码 + 修改 ~300 行现有代码 ≈ 1 天

**完成标准**：用户**完全不打开任何文件**，仅在浏览器内即可配完所有 `config.yaml` + `.env` 字段，能弹 Chromium 抓登录态，能切换演练/真实模式。

## 1.1 第 1 期文件清单

### 新建 4 个文件

| 文件 | 行数估 | 职责 |
|---|---|---|
| `review_queue/yaml_writer.py` | ~150 | 用 `ruamel.yaml` 读写 `config.yaml` 并保留注释、顺序、缩进 |
| `review_queue/env_writer.py` | ~120 | 手写解析读写 `.env`，保留注释、空行、未知键 |
| `review_queue/login_capture.py` | ~180 | 用 Playwright 弹 headed Chromium 抓番茄登录态；含登录态有效期检测 |
| `review_queue/settings_api.py` | ~400 | 聚合所有 `/api/settings/*` 端点；分 6 大类 + 模式切换 + 测试连接 |

### 修改 3 个文件

| 文件 | 改动 |
|---|---|
| `review_queue/dashboard_assets.py` | 加顶部模式横幅 + 新增 ⚙️ 设置 tab section（HTML + JS）；约 +700 行 |
| `review_queue/human_review.py` | `from review_queue.settings_api import router as settings_router` + `app.include_router(settings_router)`；约 +5 行 |
| `requirements.txt` | 加 `ruamel.yaml>=0.17` |

### 新增测试

- `tests/test_yaml_writer.py` — 验证注释/顺序保留
- `tests/test_env_writer.py` — 验证 `.env` 改单行不破坏其他行
- `tests/test_settings_api.py` — 各 6 个 section 的读写、二次确认、字段校验

## 1.2 第 1 期 API 契约

所有新端点都挂在 `/api/settings/` 命名空间下：

```
GET  /api/settings                         # 一次性返回所有配置（带遮罩）
GET  /api/settings/scheduler               # 调度小节
POST /api/settings/scheduler               # 保存调度（body: { generate_cron, review_cron, publish_cron, backup_cron, enabled, timezone })
GET  /api/settings/generation              # 生成小节（API key 已遮罩）
POST /api/settings/generation              # 保存生成
POST /api/settings/generation/test         # 测试 DeepSeek 连接（不存 key，只用提交的值发一次最小调用）
GET  /api/settings/audit                   # 审核小节
POST /api/settings/audit                   # 保存审核（含 7 维度权重，必须总和=1.0±0.005）
GET  /api/settings/publish                 # 发布小节（含登录态状态）
POST /api/settings/publish                 # 保存发布
POST /api/settings/publish/login           # 触发弹 Chromium 登录（异步，立即返回 task_id）
GET  /api/settings/publish/login/status    # 查询登录捕获进度
POST /api/settings/publish/login/finish    # 用户点"已登录完成"，关 Chromium 并保存 storage_state
GET  /api/settings/system                  # 系统小节
POST /api/settings/system                  # 保存系统
GET  /api/settings/notifications           # 通知小节（占位，第 1 期只存配置不实现通知）
POST /api/settings/notifications           # 保存通知
GET  /api/mode                             # 当前模式 { dry_run: true/false, mode: auto/semi-auto }
POST /api/mode                             # 切换模式（body: { dry_run: bool, confirmations?: [str] }）
                                           # 切到真实模式必须 confirmations 包含 "login_state_ready" 和 "accept_consequences"
```

### 字段遮罩规则

`GET /api/settings/generation` 返回的 `api_key` 字段遮罩规则：

```python
def mask_secret(value: str) -> str:
    if not value or len(value) < 12:
        return "" if not value else "•" * len(value)
    return f"{value[:6]}...{value[-4:]}"
```

POST 写入时如果客户端提交的值是遮罩字符串（含 `...`），**保留服务器现有值**（说明用户没改）。

## 1.3 第 1 期 UI 规格

### 1.3.1 顶部模式横幅（决策 11）

挂在 `<main>` 的最顶部，所有 tab 都可见：

```html
<div id="mode-banner" class="mode-banner mode-dryrun">
  <span class="mode-icon">🟦</span>
  <span class="mode-text">演练模式：模拟运行，不会真发布</span>
  <button class="mode-switch-btn">🔄 切换到真实模式</button>
</div>
```

**CSS 配色**：
- 演练模式（蓝）：`background: #dbeafe; color: #1e40af; border-left: 4px solid #2563eb;`
- 真实模式（橙）：`background: #fed7aa; color: #9a3412; border-left: 4px solid #ea580c;`

**切换交互**：
- **演练 → 真实**：弹 modal，必须勾选 2 个 checkbox 才能点【确认切换】
  ```
  ⚠️ 真实模式会真的发布到番茄小说
  请确认：
  [ ] 我已配置好登录态（点击查看状态）
  [ ] 我接受后果，准备好被发布的内容
  [取消]  [确认切换]
  ```
- **真实 → 演练**：直接切，不弹确认（降级风险更小）
- 切换成功后**所有 tab 顶部横幅同步刷新**（用 EventSource 或 polling）

### 1.3.2 新增 ⚙️ 设置 tab section

加到 sidebar：

```diff
  <button data-target="overview" class="active">总览 Dashboard</button>
  <button data-target="monitor">监控面板</button>
  <button data-target="generate">生成作品</button>
  <button data-target="review">审核队列</button>
  <button data-target="publish">发布管理</button>
  <button data-target="logs">日志查看</button>
+ <button data-target="settings-edit">⚙️ 设置</button>
  <button data-target="settings">配置说明</button>  <!-- 保留原"配置说明"作为只读帮助 -->
```

`<section id="settings-edit">` 内 6 大折叠分组（决策 9）：

```
⚙️ 设置
├── 📅 调度【展开】
│   ├── ✓ 启用全自动调度（写 scheduler.enabled）
│   ├── 4 个时间选择器组件（每个：频率 select + time input + 周几 multi-select）
│   ├── 灰色小字预览生成的 cron 表达式
│   └── ⏷ 高级
│       ├── 时区（默认 Asia/Shanghai）
│       └── （备份保留天数 — Phase 3 实现）
├── ✍️ 生成【展开】
│   ├── DeepSeek API key（遮罩 + 眼睛切换 + 测试连接按钮）
│   ├── 默认主题 / 字数 / 风格
│   └── ⏷ 高级
│       ├── model（默认 deepseek-v4-pro）
│       ├── base_url
│       ├── timeout_seconds
│       └── max_retries
├── 🔍 审核【展开】
│   ├── 通过分数线（slider 50-100，默认 85）
│   ├── 重写阈值（slider 50-90，默认 70）
│   └── ⏷ 高级
│       ├── 7 维度权重（slider 0-100% × 7 + 实时饼图 + 总和校验）
│       ├── 最大重写次数（默认 3）
│       └── 批量大小（默认 20）
├── 🚀 发布【展开】
│   ├── 🔐 番茄登录按钮 + 登录态徽章（✅有效/⚠️即将过期/❌已过期/🚫未登录）
│   ├── 发布间隔（双 slider 5-60 分钟）
│   └── ⏷ 高级
│       ├── 风控暂停开关（默认 true）
│       ├── dry-run 落库开关（默认 false）
│       └── 草稿 URL（默认 https://fanqienovel.com/）
├── 💻 系统【折叠】
│   ├── 数据库路径（只读 + 【打开文件夹】按钮）
│   ├── 日志路径（只读 + 【打开文件夹】按钮）
│   └── ⏷ 高级
│       ├── 日志级别（DEBUG/INFO/WARNING/ERROR）
│       ├── JSON 日志（开关）
│       ├── 月度预算 CNY
│       └── 每日 token 上限
└── 🔔 通知【折叠】（第 1 期占位，实际通知行为在第 2 期）
    ├── 严重通知（开关，默认 ON）
    ├── 警告通知（开关，默认 ON）
    └── 信息通知（开关，默认 OFF）
```

每节右上角放 `[💾 保存本节] [↩️ 恢复默认]` 两个按钮。

### 1.3.3 时间选择器组件（决策 5）

每个 cron 任务用一个组件：

```
[频率: 每天 ▾]  [时间: 09:00]   生成的表达式: 0 9 * * *

频率 select 选项:
  - 每天
  - 工作日（周一到周五）
  - 周末
  - 每周特定几天 → 显示 7 个 checkbox
  - 每小时（隐藏时间，显示"分钟"）
  - 每隔 N 小时
  - 自定义（直接输入 cron）
```

JS 端实时合成 cron 表达式并显示在下方。

### 1.3.4 7 维度权重控件（审核·高级）

```
plot         [━━━━●━━━━━] 20%
character    [━━━●━━━━━━] 15%
pacing       [━━━●━━━━━━] 15%
language     [━━━●━━━━━━] 15%
originality  [━━━●━━━━━━] 15%
safety       [━━●━━━━━━━] 10%
platform_fit [━━●━━━━━━━] 10%

总和: 100% ✅                [饼图实时渲染]
```

总和不等于 100% 时【💾 保存本节】按钮置灰，提示"当前总和 105%，请调整"。

饼图用 canvas 手画（30 行 JS），不引入 chart.js。

### 1.3.5 番茄登录按钮（决策 4）

按钮区域：

```
🚀 发布
└─ 番茄登录态: ✅ 有效（剩 23 天）  [🔐 重新登录]
                ⚠️ 即将过期（剩 5 天）  [🔐 重新登录]
                ❌ 已过期  [🔐 立即登录]
                🚫 未登录  [🔐 登录番茄账号]
```

点击 `[🔐 登录番茄账号]` 后的流程：

1. 前端 POST `/api/settings/publish/login` → 后端 `subprocess.Popen` 一个独立 Python 进程（避免阻塞 FastAPI），该进程：
   - `playwright.sync_playwright().chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])`
   - `context.new_page().goto("https://fanqienovel.com/")`
   - 把 Chromium 的 PID 和临时状态写到 `data/browser/.login_session.json`
2. 前端弹 modal："Chromium 已弹出，请在那个窗口里登录番茄。登录完成后点下方按钮。"
   ```
   ┌─────────────────────────────────────┐
   │ 🔐 番茄登录                          │
   │                                      │
   │ Chromium 已弹出（带橙色边框窗口）。   │
   │ 请像平时上网一样登录番茄。            │
   │                                      │
   │ ⚠️ 登录过程中如果遇到滑块/验证码，    │
   │ 请自己手动完成。软件不会绕过风控。     │
   │                                      │
   │  [取消]            [✅ 已登录完成]    │
   └─────────────────────────────────────┘
   ```
3. 前端轮询 `GET /api/settings/publish/login/status`（每 2 秒）：
   - `pending` — 等用户操作
   - `expired` — 用户超过 10 分钟没点完成，自动取消
4. 用户点【✅ 已登录完成】→ POST `/api/settings/publish/login/finish` → 后端：
   - 通过 IPC 通知子进程：`context.storage_state(path="data/browser/fansq_state.json")`
   - 子进程保存后退出
   - 删除 `.login_session.json`
   - 返回 `{ ok: true, expires_at: "..." }`
5. UI 徽章刷新为 ✅ 有效

**登录态有效期检测**：通过 `storage_state` 中 cookie 的 `expires` 字段计算，取最早过期的关键 cookie。

## 1.4 第 1 期关键代码思路

### 1.4.1 `yaml_writer.py`

```python
"""保留注释和顺序的 config.yaml 读写."""
from ruamel.yaml import YAML
from pathlib import Path
from typing import Any

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)
_yaml.width = 4096

def load_yaml(path: Path) -> Any:
    """读 yaml，返回 ruamel 的 CommentedMap（可改可写回）."""
    with path.open("r", encoding="utf-8") as f:
        return _yaml.load(f)

def save_yaml(path: Path, data: Any) -> None:
    """原子写回，保留注释/顺序/缩进."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        _yaml.dump(data, f)
    tmp.replace(path)

def update_yaml_field(path: Path, dotted_key: str, value: Any) -> None:
    """`scheduler.generate_cron` 这种点路径设置单个字段."""
    data = load_yaml(path)
    keys = dotted_key.split(".")
    current = data
    for k in keys[:-1]:
        current = current[k]
    current[keys[-1]] = value
    save_yaml(path, data)
```

### 1.4.2 `env_writer.py`

```python
"""保留注释和空行的 .env 读写。不依赖 python-dotenv 的写回（它会重排）."""
import re
from pathlib import Path

_LINE_RE = re.compile(r"^(?P<key>[A-Z_][A-Z0-9_]*)\s*=\s*(?P<value>.*)$")

def read_env(path: Path) -> dict[str, str]:
    """返回 key→value 字典，忽略注释和空行."""
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _LINE_RE.match(s)
        if m:
            out[m.group("key")] = m.group("value").strip().strip('"').strip("'")
    return out

def write_env_field(path: Path, key: str, value: str) -> None:
    """改单个字段，保留所有注释/空行/其他键的顺序。
    
    - 如果 key 已存在：原地替换该行
    - 如果 key 不存在：追加到文件末尾
    - 写入用临时文件 + rename 保证原子
    """
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    found = False
    new_lines = []
    quoted_value = _quote_if_needed(value)
    
    for line in lines:
        m = _LINE_RE.match(line.strip())
        if m and m.group("key") == key:
            new_lines.append(f"{key}={quoted_value}")
            found = True
        else:
            new_lines.append(line)
    
    if not found:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append(f"{key}={quoted_value}")
    
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    tmp.replace(path)
    # Windows 上限制权限
    try:
        import stat
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass

def _quote_if_needed(value: str) -> str:
    if any(c in value for c in " \t#"):
        return f'"{value}"'
    return value
```

### 1.4.3 `login_capture.py` 思路

```python
"""弹真实 Chromium 让用户手动登录番茄并保存登录态."""
import json
import subprocess
import sys
from pathlib import Path

SESSION_FILE = Path("data/browser/.login_session.json")
STATE_FILE = Path("data/browser/fansq_state.json")
LOGIN_URL = "https://fanqienovel.com/"
TIMEOUT_SECONDS = 600  # 10 分钟超时

def start_login_session() -> dict:
    """启动子进程弹 Chromium。返回 { task_id, started_at }."""
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, "-m", "review_queue.login_capture", "--worker"],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    session = {"task_id": str(proc.pid), "started_at": datetime.now().isoformat(), "status": "pending"}
    SESSION_FILE.write_text(json.dumps(session))
    return session

def get_session_status() -> dict:
    if not SESSION_FILE.exists():
        return {"status": "none"}
    return json.loads(SESSION_FILE.read_text())

def finish_login_session() -> dict:
    """通知子进程保存 storage_state 并退出."""
    # 用一个 trigger 文件做 IPC（最简方案）
    Path("data/browser/.login_trigger_finish").touch()
    # 等子进程退出（最多 30 秒）
    ...
    return {"ok": True, "expires_at": _detect_expires_from_storage_state()}

def login_state_validity() -> dict:
    """读 storage_state.json 计算最早 cookie 过期时间."""
    if not STATE_FILE.exists():
        return {"status": "missing"}
    state = json.loads(STATE_FILE.read_text())
    cookies = state.get("cookies", [])
    if not cookies:
        return {"status": "empty"}
    expires = [c.get("expires", -1) for c in cookies if c.get("expires", -1) > 0]
    if not expires:
        return {"status": "session_only"}  # session cookie 无明确过期
    earliest = min(expires)
    days_left = (earliest - time.time()) / 86400
    if days_left < 0:
        return {"status": "expired", "days_left": 0}
    if days_left < 7:
        return {"status": "expiring", "days_left": int(days_left)}
    return {"status": "valid", "days_left": int(days_left)}

# Worker 模式（subprocess 内执行）
if __name__ == "__main__" and "--worker" in sys.argv:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(LOGIN_URL)
        # 轮询 trigger 文件或超时
        trigger = Path("data/browser/.login_trigger_finish")
        deadline = time.time() + TIMEOUT_SECONDS
        while time.time() < deadline:
            if trigger.exists():
                STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                context.storage_state(path=str(STATE_FILE))
                trigger.unlink()
                break
            time.sleep(1)
        browser.close()
        SESSION_FILE.unlink(missing_ok=True)
```

> ⚠️ **重要**：登录捕获必须用**子进程**而非线程，因为 Playwright sync API 不能在 FastAPI 的 async event loop 里直接跑。

### 1.4.4 `settings_api.py` 骨架

```python
"""所有 ⚙️ 设置 tab 后端 API."""
from fastapi import APIRouter, HTTPException, Request
from review_queue.yaml_writer import update_yaml_field, load_yaml
from review_queue.env_writer import read_env, write_env_field
from review_queue.login_capture import (
    start_login_session, get_session_status, 
    finish_login_session, login_state_validity
)
from config_loader import DEFAULT_CONFIG_PATH, DEFAULT_DOTENV_PATH, load_from_environment

router = APIRouter(prefix="/api/settings")

# ===== 通用工具 =====

def mask_secret(value: str) -> str:
    if not value: return ""
    if len(value) < 12: return "•" * len(value)
    return f"{value[:6]}...{value[-4:]}"

def is_masked(value: str) -> bool:
    return "..." in value and value.startswith(value[:6])

# ===== 调度小节 =====

@router.get("/scheduler")
def get_scheduler():
    cfg = load_yaml(DEFAULT_CONFIG_PATH)
    s = cfg.get("scheduler", {})
    return {
        "enabled": s.get("enabled", False),
        "timezone": s.get("timezone", "Asia/Shanghai"),
        "generate_cron": s.get("generate_cron", "0 9 * * *"),
        "review_cron": s.get("review_cron", "30 9 * * *"),
        "publish_cron": s.get("publish_cron", "0 10 * * *"),
        "backup_cron": s.get("backup_cron", "0 3 * * *"),
    }

@router.post("/scheduler")
async def set_scheduler(request: Request):
    payload = await request.json()
    _validate_cron(payload.get("generate_cron"))
    _validate_cron(payload.get("review_cron"))
    _validate_cron(payload.get("publish_cron"))
    _validate_cron(payload.get("backup_cron"))
    cfg = load_yaml(DEFAULT_CONFIG_PATH)
    cfg["scheduler"]["enabled"] = bool(payload.get("enabled"))
    cfg["scheduler"]["timezone"] = str(payload.get("timezone", "Asia/Shanghai"))
    cfg["scheduler"]["generate_cron"] = payload["generate_cron"]
    cfg["scheduler"]["review_cron"] = payload["review_cron"]
    cfg["scheduler"]["publish_cron"] = payload["publish_cron"]
    cfg["scheduler"]["backup_cron"] = payload["backup_cron"]
    save_yaml(DEFAULT_CONFIG_PATH, cfg)
    return {"ok": True}

# ===== 生成小节（含 API key 遮罩 + 测试连接）=====

@router.get("/generation")
def get_generation():
    env = read_env(DEFAULT_DOTENV_PATH)
    cfg = load_yaml(DEFAULT_CONFIG_PATH)
    g = cfg.get("generation", {})
    d = cfg.get("deepseek", {})
    return {
        "api_key_masked": mask_secret(env.get("DEEPSEEK_API_KEY", "")),
        "base_url": env.get("DEEPSEEK_BASE_URL") or d.get("base_url"),
        "model": env.get("DEEPSEEK_MODEL") or d.get("model"),
        "default_theme": g.get("theme"),
        "default_word_count": g.get("word_count"),
        "default_style": g.get("style"),
        "timeout_seconds": d.get("timeout_seconds", 60),
        "max_retries": d.get("max_retries", 3),
    }

@router.post("/generation")
async def set_generation(request: Request):
    payload = await request.json()
    new_key = payload.get("api_key", "")
    # 如果是遮罩格式，跳过不写
    if new_key and not is_masked(new_key):
        write_env_field(DEFAULT_DOTENV_PATH, "DEEPSEEK_API_KEY", new_key)
    if payload.get("base_url"):
        write_env_field(DEFAULT_DOTENV_PATH, "DEEPSEEK_BASE_URL", payload["base_url"])
    if payload.get("model"):
        write_env_field(DEFAULT_DOTENV_PATH, "DEEPSEEK_MODEL", payload["model"])
    cfg = load_yaml(DEFAULT_CONFIG_PATH)
    cfg["generation"]["theme"] = payload.get("default_theme", cfg["generation"].get("theme"))
    cfg["generation"]["word_count"] = int(payload.get("default_word_count", 3000))
    cfg["generation"]["style"] = payload.get("default_style", "")
    cfg["deepseek"]["timeout_seconds"] = int(payload.get("timeout_seconds", 60))
    cfg["deepseek"]["max_retries"] = int(payload.get("max_retries", 3))
    save_yaml(DEFAULT_CONFIG_PATH, cfg)
    return {"ok": True}

@router.post("/generation/test")
async def test_deepseek(request: Request):
    payload = await request.json()
    test_key = payload.get("api_key", "")
    test_url = payload.get("base_url")
    test_model = payload.get("model", "deepseek-v4-pro")
    if is_masked(test_key):
        # 用现有 .env 里的 key
        test_key = read_env(DEFAULT_DOTENV_PATH).get("DEEPSEEK_API_KEY", "")
    if not test_key:
        return {"ok": False, "message": "API key 为空"}
    # 发一次最小请求验证（用 OpenAI 兼容协议）
    try:
        import httpx
        resp = httpx.post(
            f"{test_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {test_key}", "Content-Type": "application/json"},
            json={"model": test_model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
            timeout=15.0,
        )
        if resp.status_code == 200:
            return {"ok": True, "message": "✅ 连接成功"}
        return {"ok": False, "message": f"❌ HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"ok": False, "message": f"❌ {type(e).__name__}: {e}"}

# ===== 审核 / 发布 / 系统 / 通知小节同理 =====

# ===== 番茄登录 =====

@router.post("/publish/login")
def trigger_login():
    return start_login_session()

@router.get("/publish/login/status")
def login_status():
    return get_session_status() | login_state_validity()

@router.post("/publish/login/finish")
def login_finish():
    return finish_login_session()

# ===== 模式切换 =====

@router.get("/mode")  # 注意挂在 /api/mode 而非 /api/settings/mode
def get_mode():
    cfg = load_from_environment()
    return {"dry_run": cfg.is_dry_run, "mode": cfg.data.get("runtime", {}).get("mode")}

@router.post("/mode")
async def set_mode(request: Request):
    payload = await request.json()
    new_dry_run = bool(payload.get("dry_run"))
    if not new_dry_run:  # 切换到真实模式
        confirmations = set(payload.get("confirmations", []))
        required = {"login_state_ready", "accept_consequences"}
        if not required.issubset(confirmations):
            raise HTTPException(400, f"切换到真实模式需要确认: {required - confirmations}")
    update_yaml_field(DEFAULT_CONFIG_PATH, "runtime.dry_run", new_dry_run)
    # 写日志
    logger.info("模式切换: dry_run -> %s by local_ui", new_dry_run)
    return {"ok": True, "dry_run": new_dry_run}
```

> 注意：`/api/mode` 在路由器 `prefix="/api/settings"` 下会变成 `/api/settings/mode`。要让它在 `/api/mode` 路径，需要单独建第二个 router 或用绝对路径方法。

## 1.5 第 1 期前端 JS 关键片段

### Cron 表达式合成

```javascript
function buildCron(frequency, time, weekdays) {
  const [h, m] = time.split(":");
  switch (frequency) {
    case "daily":      return `${m} ${h} * * *`;
    case "weekday":    return `${m} ${h} * * 1-5`;
    case "weekend":    return `${m} ${h} * * 0,6`;
    case "weekly":     return `${m} ${h} * * ${weekdays.join(",")}`;
    case "hourly":     return `${m} * * * *`;
    case "every_n_hours": {
      const n = parseInt(weekdays[0]);  // 复用 weekdays 字段传 N
      return `${m} */${n} * * *`;
    }
    case "custom":     return weekdays[0];  // 自由输入
  }
}
```

### 7 维度饼图

```javascript
function drawWeightPie(canvas, weights) {
  const ctx = canvas.getContext("2d");
  const total = Object.values(weights).reduce((a,b) => a+b, 0);
  const colors = ["#2563eb", "#16a34a", "#d97706", "#dc2626", "#0284c7", "#7c3aed", "#db2777"];
  let start = -Math.PI / 2;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  Object.entries(weights).forEach(([key, val], i) => {
    const angle = (val / total) * 2 * Math.PI;
    ctx.fillStyle = colors[i];
    ctx.beginPath();
    ctx.moveTo(canvas.width/2, canvas.height/2);
    ctx.arc(canvas.width/2, canvas.height/2, Math.min(canvas.width, canvas.height)/2 - 4, start, start + angle);
    ctx.closePath();
    ctx.fill();
    start += angle;
  });
}
```

### API key 头尾遮罩 + 眼睛切换

```javascript
function bindSecretInput(inputEl, toggleBtn) {
  let isShowing = false;
  inputEl.type = "password";
  toggleBtn.addEventListener("click", () => {
    isShowing = !isShowing;
    inputEl.type = isShowing ? "text" : "password";
    toggleBtn.textContent = isShowing ? "🙈" : "👁";
  });
  inputEl.addEventListener("blur", () => {
    inputEl.type = "password";
    toggleBtn.textContent = "👁";
    isShowing = false;
  });
}
```

## 1.6 第 1 期验收标准

执行如下步骤，全部应通过：

```bash
# 1. 安装依赖
cd D:\Development_alma\anp
.\.venv\Scripts\activate
pip install -r requirements.txt

# 2. 跑现有测试不能破
pytest -q
# 期望: 全部 pass

# 3. 跑新测试
pytest -q tests/test_yaml_writer.py tests/test_env_writer.py tests/test_settings_api.py
# 期望: 全部 pass，覆盖率 ≥ 80%

# 4. 启动 UI
python -m review_queue.human_review --port 18000
# 浏览器访问 http://127.0.0.1:18000
```

**手动验收清单**：

- [ ] 顶部蓝色"演练模式"横幅可见
- [ ] 点击【🔄 切换到真实模式】弹 modal，必须勾选 2 项才能确认
- [ ] 切到真实模式后横幅变橙色
- [ ] 真实模式 → 演练模式 直接切，不弹确认
- [ ] 切换写入了 `config.yaml` 的 `runtime.dry_run` 字段，**注释和顺序保留**
- [ ] ⚙️ 设置 tab 出现在 sidebar
- [ ] 6 大类正确折叠/展开
- [ ] 调度小节：4 个时间选择器工作正常，灰色字预览 cron
- [ ] 生成小节：API key 显示 `sk-87b...1561`（遮罩）
- [ ] 生成小节：编辑时支持眼睛切换 + 失焦自动遮罩
- [ ] 生成小节：【🔍 测试连接】成功返回 ✅
- [ ] 审核小节：滑块和饼图同步更新
- [ ] 审核小节：权重总和不等于 100% 时保存按钮置灰
- [ ] 发布小节：登录态徽章正确显示当前状态
- [ ] 发布小节：【🔐 登录番茄账号】真的弹出独立 Chromium 窗口
- [ ] Chromium 窗口里手动登录后，【✅ 已登录完成】按钮触发保存 `fansq_state.json`
- [ ] 系统小节：路径只读，【打开文件夹】真的打开 Explorer
- [ ] 各小节【💾 保存本节】写入正确，**`config.yaml` 注释/顺序保留**
- [ ] 各小节【↩️ 恢复默认】恢复初始值（不立即写盘，需点保存）
- [ ] 改完字段后**调度器/生成器即时生效**（不需重启 `python main.py`）
- [ ] `.env` 文件改 `DEEPSEEK_API_KEY` 后**注释和其他键的顺序保留**

## 1.7 第 1 期注意事项

1. **`config.yaml` 的写回必须用 `ruamel.yaml`**，绝对不能用 `yaml.safe_dump`（会丢失注释和顺序）
2. **`.env` 写回不能用 `python-dotenv` 的 `set_key`**（同样会丢失注释顺序）
3. **API key 永不通过 GET 返回明文**——总是先 `mask_secret` 再返回
4. **POST 请求里如果 api_key 字段是遮罩格式（含 `...`）**，说明用户没改，**不要写回 .env**
5. **登录捕获必须用 subprocess 启子进程**，不能在 FastAPI handler 里直接 `sync_playwright()`
6. **登录 storage_state 文件路径**：默认 `data/browser/fansq_state.json`，要尊重 `FANSQ_LOGIN_STATE_PATH` 环境变量
7. **模式切换横幅** 用 EventSource (SSE) 或 5 秒轮询同步——多 tab 打开时要全部同步
8. **每个小节的【保存】调用要返回明确的 success/failure**，前端 toast 显示
9. **校验失败要给中文消息**："发布间隔最小值不能大于最大值" 而非 "validation error"
10. **不要重写 `config_loader.py`**——它已经能正确读取，新代码只负责写

---

# 🥈 第 2 期：托盘 + 全自动后台化

**预估工作量**：800-1200 行新代码 + 修改 `start_anp.bat` ≈ 半天到 1 天

**完成标准**：双击桌面图标 → 无黑窗口 → 托盘出现 → 浏览器自动开 → 关浏览器后台还在跑 → 重启 Windows 后软件自动醒来 → 出事时 Windows 右下角弹通知。

## 2.1 第 2 期文件清单

### 新建 4 个文件

| 文件 | 行数估 | 职责 |
|---|---|---|
| `tray_app.py` | ~350 | 托盘主进程，启动 uvicorn + APScheduler 子进程，管理状态色 |
| `tray_icons/__init__.py` | - | 空文件（包标识） |
| `tray_icons/generator.py` | ~80 | 用 Pillow 生成 4 色图标（绿/黄/红/灰）的 PNG bytes，避免依赖外部图片文件 |
| `auto_start.py` | ~100 | 写/删 `shell:startup\\ANP.bat` 快捷方式 |
| `notification_bus.py` | ~150 | 通知中心，统一管理"严重/警告/信息"三档；前端 toast + 托盘色 + Windows 桌面通知 |

### 修改 5 个文件

| 文件 | 改动 |
|---|---|
| `start_anp.bat` | 改成调用 `pythonw tray_app.py`（无控制台窗口），不再直接 `python -m review_queue.human_review` |
| `requirements.txt` | 加 `pystray>=0.19`、`Pillow>=10.0`、`pywin32>=306` |
| `review_queue/human_review.py` | 加 `/api/notifications/stream` SSE 端点；启动 hook 通知托盘；优雅关闭 |
| `scheduler.py` | 关键事件（成功/失败/暂停）通过 notification_bus 发出；新增 `next_run_time()` |
| `main.py` | 加 `--tray` 参数，标记当前是否被托盘启动（影响日志/退出行为） |

### 新增测试

- `tests/test_auto_start.py` — 写/删 `.bat` 快捷方式的副作用
- `tests/test_notification_bus.py` — 通知分级、订阅、批量
- `tests/test_tray_icons.py` — 图标生成是有效 PNG bytes
- ⚠️ `tests/test_tray_app.py` 用 mock，不真启托盘（CI 跑不了 GUI）

## 2.2 第 2 期托盘程序架构

```
tray_app.py (主进程)
├── pystray.Icon（系统托盘图标）
│   ├── 状态: green / yellow / red / gray
│   ├── 菜单:
│   │   ├── 📊 打开管理界面 → webbrowser.open("http://127.0.0.1:18000")
│   │   ├── ▶️ 启动全自动 / ⏸️ 暂停全自动（动态切换）
│   │   ├── ─────
│   │   ├── 🔄 重启服务
│   │   ├── 🌐 在浏览器中打开
│   │   ├── 📁 打开数据文件夹
│   │   ├── 📄 打开日志文件
│   │   ├── ─────
│   │   └── 🚪 退出
│   └── on_double_click → 打开管理界面
├── 子进程管理
│   ├── uvicorn (review_queue.human_review:app) — 始终在跑
│   └── scheduler (main.py --mode auto) — 可被启停（取决于全自动开关）
├── 状态轮询
│   └── 每 5 秒调用 http://127.0.0.1:18000/api/health 决定图标颜色
├── 通知监听
│   └── EventSource 订阅 /api/notifications/stream
│   └── 收到 critical/warning 弹 Windows 桌面通知（pystray.notify）
└── 优雅退出
    └── 终止子进程，清理 SESSION_FILE / pid 文件
```

## 2.3 第 2 期 API 增量

```
GET  /api/health                              # 健康探针，托盘 5 秒轮询
                                              # 返回 { status: ok|degraded|down, scheduler_running: bool, ... }
GET  /api/notifications/stream                # SSE，推送实时通知
POST /api/notifications/dismiss/{id}          # 标记已读
GET  /api/control/auto                        # 当前 auto 模式开/关
POST /api/control/auto                        # body: { enabled: bool }
                                              # enabled=true 启动调度器；enabled=false 停止
POST /api/control/restart                     # 软重启 uvicorn（托盘菜单触发）
GET  /api/autostart                           # 当前是否开机自启
POST /api/autostart                           # body: { enabled: bool }
                                              # 写/删 shell:startup\\ANP.bat
```

新增 `⚙️ 设置 → 系统` 小节里的两个开关：

```
💻 系统
├── ☐ 启用全自动模式（启动调度器，立即生效）  -- POST /api/control/auto
└── ☐ 开机自启动（Windows 启动时自动运行 ANP）  -- POST /api/autostart
```

## 2.4 第 2 期 `auto_start.py` 关键代码

```python
"""写/删 Windows 启动文件夹的快捷方式（不需要管理员权限）."""
import os
import sys
from pathlib import Path

def startup_folder() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"

ANP_LAUNCHER_NAME = "ANP_AutoStart.bat"
ANP_PROJECT_ROOT = Path(__file__).resolve().parent

def is_enabled() -> bool:
    return (startup_folder() / ANP_LAUNCHER_NAME).exists()

def enable() -> None:
    """写一个 .bat 到 startup 文件夹，启动时静默调用 tray_app.py."""
    target = startup_folder() / ANP_LAUNCHER_NAME
    pythonw = ANP_PROJECT_ROOT / ".venv" / "Scripts" / "pythonw.exe"
    tray_script = ANP_PROJECT_ROOT / "tray_app.py"
    content = (
        f"@echo off\n"
        f"cd /d \"{ANP_PROJECT_ROOT}\"\n"
        f"timeout /t 10 /nobreak >nul\n"   # 等开机后 10 秒，避免抢资源
        f"start \"\" /B \"{pythonw}\" \"{tray_script}\"\n"
    )
    target.write_text(content, encoding="utf-8")

def disable() -> None:
    target = startup_folder() / ANP_LAUNCHER_NAME
    if target.exists():
        target.unlink()
```

> ⚠️ 用 `pythonw.exe` 而非 `python.exe`，前者不弹控制台窗口。

## 2.5 第 2 期托盘图标生成（避免外部图片依赖）

```python
"""动态生成 4 色托盘图标，避免依赖外部 .ico 文件."""
from PIL import Image, ImageDraw

def make_icon(color: str, badge: int = 0) -> Image.Image:
    """生成 64x64 圆形纯色图标，可选右下角数字角标."""
    colors = {
        "green":  "#16a34a",
        "yellow": "#d97706",
        "red":    "#dc2626",
        "gray":   "#64748b",
    }
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill=colors[color])
    # 写 "A" 字母代表 ANP
    try:
        from PIL import ImageFont
        font = ImageFont.truetype("arial.ttf", 32)
        draw.text((22, 14), "A", fill="white", font=font)
    except Exception:
        draw.text((22, 22), "A", fill="white")
    if badge > 0:
        draw.ellipse((42, 42, 62, 62), fill="#dc2626")
        draw.text((47, 44), str(min(badge, 9)), fill="white")
    return img
```

## 2.6 第 2 期 `tray_app.py` 主循环

```python
"""ANP 系统托盘主程序."""
import threading
import subprocess
import sys
import webbrowser
from pathlib import Path

import pystray
import httpx

from tray_icons.generator import make_icon
from auto_start import is_enabled as autostart_enabled

PROJECT_ROOT = Path(__file__).resolve().parent
UI_URL = "http://127.0.0.1:18000"
HEALTH_URL = f"{UI_URL}/api/health"
NOTIF_STREAM_URL = f"{UI_URL}/api/notifications/stream"

class TrayApp:
    def __init__(self):
        self.uvicorn_proc = None
        self.scheduler_proc = None
        self.icon = pystray.Icon(
            "ANP",
            icon=make_icon("gray"),
            title="ANP（启动中…）",
            menu=self._build_menu(),
        )

    def start(self):
        self._launch_uvicorn()
        threading.Thread(target=self._poll_health, daemon=True).start()
        threading.Thread(target=self._listen_notifications, daemon=True).start()
        self.icon.run(setup=lambda i: webbrowser.open(UI_URL))

    def _launch_uvicorn(self):
        self.uvicorn_proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "review_queue.human_review:app",
             "--host", "127.0.0.1", "--port", "18000"],
            cwd=PROJECT_ROOT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def _poll_health(self):
        while True:
            try:
                r = httpx.get(HEALTH_URL, timeout=2.0)
                data = r.json()
                if data["status"] == "ok":
                    self.icon.icon = make_icon("green")
                    self.icon.title = "ANP（运行中，全自动已启用）"
                elif data["status"] == "degraded":
                    self.icon.icon = make_icon("yellow")
                    self.icon.title = "ANP（运行中，有警告）"
                else:
                    self.icon.icon = make_icon("red")
                    self.icon.title = "ANP（错误）"
            except Exception:
                self.icon.icon = make_icon("gray")
                self.icon.title = "ANP（连接断开）"
            time.sleep(5)

    def _listen_notifications(self):
        # SSE 客户端
        with httpx.stream("GET", NOTIF_STREAM_URL, timeout=None) as r:
            for line in r.iter_lines():
                if line.startswith("data: "):
                    notif = json.loads(line[6:])
                    if notif["severity"] in ("critical", "warning"):
                        self.icon.notify(notif["message"], notif["title"])

    def _build_menu(self):
        return pystray.Menu(
            pystray.MenuItem("📊 打开管理界面", lambda: webbrowser.open(UI_URL), default=True),
            pystray.MenuItem(
                lambda i: "▶️ 启动全自动" if not self._auto_running() else "⏸️ 暂停全自动",
                self._toggle_auto,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🔄 重启服务", self._restart_uvicorn),
            pystray.MenuItem("📁 打开数据文件夹", lambda: subprocess.Popen(["explorer", str(PROJECT_ROOT/"data")])),
            pystray.MenuItem("📄 打开日志文件", lambda: subprocess.Popen(["notepad", str(PROJECT_ROOT/"logs/anp.log")])),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🚪 退出", self._quit),
        )

    def _auto_running(self):
        try:
            return httpx.get(f"{UI_URL}/api/control/auto").json()["enabled"]
        except Exception:
            return False

    def _toggle_auto(self):
        current = self._auto_running()
        httpx.post(f"{UI_URL}/api/control/auto", json={"enabled": not current})

    def _quit(self):
        if self.uvicorn_proc: self.uvicorn_proc.terminate()
        if self.scheduler_proc: self.scheduler_proc.terminate()
        self.icon.stop()

if __name__ == "__main__":
    TrayApp().start()
```

## 2.7 第 2 期改造 `start_anp.bat`

把现有 `start_anp.bat` 末尾改成：

```bat
:: 原有的环境检查、依赖安装、目录初始化全部保留

:: 关键改动：用 pythonw 静默启动托盘程序
echo [5/5] 启动 ANP（托盘模式）...
start "" /B ".venv\Scripts\pythonw.exe" "tray_app.py"
echo ✅ ANP 已启动到系统托盘。
echo （右下角任务栏可见 ANP 图标，双击打开界面）
exit /b 0
```

旧的 `python -m review_queue.human_review --host ... --port ...` 那一行删掉。

## 2.8 第 2 期通知总线 `notification_bus.py`

```python
"""通知中心：scheduler / publisher 等触发事件，前端 SSE + 托盘订阅。"""
import asyncio
from collections import deque
from datetime import datetime
from enum import Enum
from typing import AsyncIterator

class Severity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"

class Notification:
    def __init__(self, severity, title, message, source="system"):
        self.id = str(uuid.uuid4())
        self.severity = severity
        self.title = title
        self.message = message
        self.source = source
        self.created_at = datetime.now()
        self.dismissed = False

class NotificationBus:
    def __init__(self, capacity: int = 200):
        self._buffer = deque(maxlen=capacity)
        self._subscribers: list[asyncio.Queue] = []

    def publish(self, severity, title, message, source="system"):
        n = Notification(severity, title, message, source)
        self._buffer.appendleft(n)
        for q in self._subscribers:
            q.put_nowait(n)

    async def subscribe(self) -> AsyncIterator[Notification]:
        q = asyncio.Queue()
        self._subscribers.append(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subscribers.remove(q)

    def list_recent(self, limit: int = 50) -> list[Notification]:
        return list(self._buffer)[:limit]

# 全局单例
bus = NotificationBus()
```

`scheduler.py` 内的关键事件接入：

```python
from review_queue.notification_bus import bus, Severity

# 在 scheduled_publish 里:
if result.status == PublishStatus.PAUSED_RISK_CONTROL:
    bus.publish(Severity.CRITICAL, "番茄风控暂停", f"作品 #{story.id} 触发风控，已暂停")
elif result.status == PublishStatus.PUBLISHED:
    bus.publish(Severity.INFO, "发布成功", f"作品 #{story.id} 已发到番茄")
```

## 2.9 第 2 期验收标准

```bash
# 1. 安装新依赖
.\.venv\Scripts\activate
pip install -r requirements.txt

# 2. 确认 pythonw.exe 存在
dir .\.venv\Scripts\pythonw.exe

# 3. 双击 start_anp.bat
```

**手动验收清单**：

- [ ] 双击 `start_anp.bat` 后**不再有黑窗口残留**（最多闪一下 cmd）
- [ ] 任务栏右下角出现 ANP 灰色图标
- [ ] 5 秒内图标变绿（uvicorn 起来后）
- [ ] 默认浏览器自动打开 `http://127.0.0.1:18000`
- [ ] 关掉浏览器，托盘图标仍在
- [ ] 右键托盘图标，菜单项全部正常工作
- [ ] 点【⏸️ 暂停全自动】后图标变灰，菜单文字变成【▶️ 启动全自动】
- [ ] 点【🚪 退出】托盘消失，uvicorn 和 scheduler 子进程全部死掉（任务管理器验证）
- [ ] ⚙️ 设置 → 系统 勾选【开机自启动】，去 `shell:startup` 文件夹（`Win+R` → `shell:startup`）能看到 `ANP_AutoStart.bat`
- [ ] 重启 Windows 后 10 秒内托盘图标自动出现
- [ ] 取消勾选【开机自启动】，`shell:startup` 里的 .bat 消失
- [ ] 模拟一次番茄风控（手动触发 `bus.publish(Severity.CRITICAL, ...)`）→ Windows 右下角弹气泡 + 托盘图标变红

## 2.10 第 2 期注意事项

1. **`pythonw.exe` vs `python.exe`**：托盘必须用 `pythonw`，否则会有黑窗口
2. **`creationflags=CREATE_NO_WINDOW`**：所有 `subprocess.Popen` 启动 Python 子进程都要加这个 flag
3. **uvicorn 子进程优雅退出**：托盘退出时要 `proc.terminate()` + 等 5 秒 + 必要时 `proc.kill()`
4. **避免重复启动**：托盘启动时检查 `127.0.0.1:18000` 是否已占用，已占用则不再启 uvicorn 子进程（直接连现有的）
5. **HEALTH_URL 超时一定要短**（2 秒）——否则托盘卡住
6. **SSE 长连接断了要重连**：服务重启后 EventSource 自动重连
7. **`shell:startup` 文件夹**：用 `os.environ["APPDATA"]` 拼路径，不要写死 `C:\Users\...\AppData\Roaming`
8. **autostart 的 .bat 用绝对路径**：用户安装路径不一定是 `D:\Development_alma\anp`
9. **托盘菜单项的文字动态变化**：用 lambda 而非字符串，pystray 会每次显示菜单时重算
10. **`pywin32` 在某些 Python 安装下需要额外跑** `python -m pywin32_postinstall -install`，文档要说明

---

# 🥉 第 3 期：Dashboard 状态卡片 + 打磨

**预估工作量**：500-800 行新代码 + 修改 ~200 行现有代码 ≈ 半天

**完成标准**：用户打开总览 Dashboard **3 秒内**判断"我的全自动健康吗"；演练数据和真实数据有视觉区分；通知按严重等级分流到不同 UI 位置。

## 3.1 第 3 期文件清单

### 修改 4 个文件

| 文件 | 改动 |
|---|---|
| `review_queue/dashboard_assets.py` | Dashboard 顶部加 4 张状态卡片 HTML + 5 秒轮询 JS（约 +250 行） |
| `review_queue/human_review.py` | 加 `/api/monitor/cards` 聚合端点（约 +60 行） |
| `scheduler.py` | 暴露 `next_run_time(job_id)`、`last_run_summary()` 方法（约 +50 行） |
| `review_queue/dashboard_assets.py` | 演练样本视觉标记（CSS class `.dryrun-marker`） |

### 新建 1 个文件

| 文件 | 行数估 | 职责 |
|---|---|---|
| `review_queue/monitor_aggregator.py` | ~200 | 聚合 4 张卡片所需数据：下次运行/最近结果/登录态/本月预算 |

## 3.2 第 3 期 API 增量

```
GET  /api/monitor/cards            # 4 张卡片聚合数据，5 秒一次
                                   # 返回:
                                   # {
                                   #   "next_run": { job: "generate", at: "2026-04-30T09:00:00", countdown_seconds: 32400 },
                                   #   "last_run": { job: "publish", at: "...", status: "success", story_id: 42 },
                                   #   "login_state": { status: "valid", days_left: 23 },
                                   #   "budget": { used_cny: 23.4, limit_cny: 100, percent: 23.4 }
                                   # }
```

## 3.3 第 3 期 4 张卡片 UI 规格

加在总览 Dashboard 顶部（保留现有的"最近 12 篇作品"和"系统提醒"）：

```html
<div class="status-cards">
  <div class="card-status card-status-success" data-card="next-run">
    <div class="card-icon">⏰</div>
    <div class="card-label">下次运行</div>
    <div class="card-value">09:00</div>
    <div class="card-detail">生成任务 · 倒计时 8h 59m</div>
  </div>
  
  <div class="card-status card-status-success" data-card="last-run">
    <div class="card-icon">✅</div>
    <div class="card-label">最近结果</div>
    <div class="card-value">成功</div>
    <div class="card-detail">发布 #42 · 2 分钟前</div>
  </div>
  
  <div class="card-status card-status-warning" data-card="login-state">
    <div class="card-icon">🔐</div>
    <div class="card-label">番茄登录态</div>
    <div class="card-value">5 天</div>
    <div class="card-detail">⚠️ 即将过期，请重新登录</div>
  </div>
  
  <div class="card-status card-status-success" data-card="budget">
    <div class="card-icon">💰</div>
    <div class="card-label">本月预算</div>
    <div class="card-value">¥23.4 / ¥100</div>
    <div class="card-detail">23%</div>
  </div>
</div>
```

**警示色规则**：

| 卡片 | 绿（success） | 黄（warning） | 红（danger） |
|---|---|---|---|
| 下次运行 | 调度器在跑 | - | 调度器停了 |
| 最近结果 | 上次成功 | 1 次失败但已重试 | 连续 2+ 次失败 |
| 登录态 | > 7 天 | 1-7 天 | < 1 天 或已过期 |
| 本月预算 | < 60% | 60-90% | > 90% |

**点击行为**：
- 点【下次运行】→ 跳⚙️ 设置 → 调度小节
- 点【最近结果】→ 跳日志 tab，自动滚到该次记录
- 点【登录态】→ 跳⚙️ 设置 → 发布小节，焦点放在登录按钮
- 点【本月预算】→ 跳⚙️ 设置 → 系统 → 高级（预算行）

## 3.4 第 3 期演练数据视觉标记

任何由 dry-run 模式产生的 story 在 review_queue 表里加一列 `is_dry_run: bool`（数据库迁移）。UI 上显示为：

- 列表项前加 📋 emoji
- CSS class `.story-dryrun` 应用浅蓝色左边框
- Tooltip：「此作品由演练模式生成，未真正发布」

迁移：

```sql
ALTER TABLE stories ADD COLUMN is_dry_run INTEGER NOT NULL DEFAULT 0;
```

`db.py` 里 `insert_story()` 接受 `is_dry_run` 参数，从配置读取当前模式自动设置。

## 3.5 第 3 期通知精细化

第 2 期已经有了 `notification_bus`，第 3 期细化触发规则：

| 事件 | 严重级 | 标题 | 触发位置 |
|---|---|---|---|
| 番茄风控暂停 | critical | 🚨 番茄风控触发 | `publisher/fansq.py` |
| 登录态过期 | critical | 🔐 登录态已失效 | `monitor_aggregator.py` 心跳 |
| 月预算超 100% | critical | 💸 月预算超支 | `cost_limits` 检查点 |
| 连续 3 次 API 失败 | critical | ❌ DeepSeek 持续故障 | `generator/api_client.py` |
| 单次 API 失败 | warning | ⚠️ DeepSeek 调用失败（已重试） | 同上 |
| 登录态 7 天内过期 | warning | 🔐 登录态即将过期 | 心跳 |
| 月预算超 80% | warning | 💰 月预算 80% | cost_limits |
| AI 审核全部 needs_human | warning | 📋 审核全部需人工 | `ai_review.py` |
| 单篇生成完成 | info | ✓ 生成完成 | `scheduler.py` |
| 发布成功 | info | ✓ 发布成功 | `scheduler.py` |
| 备份完成 | info | ✓ 备份完成 | `scheduler.py` |

**前端三档分流**：

- **critical**：顶部红色横幅（不消失，需手动 dismiss） + 桌面气泡 + 托盘红
- **warning**：右下角 toast（10 秒淡出） + 桌面气泡（无声） + 托盘黄
- **info**：右下角 toast（3 秒淡出） + 不弹桌面通知 + 托盘绿（不变色）

**不打扰时段**（⚙️ 设置 → 通知 → 高级）：可以设 22:00 - 08:00 内只送 critical。

## 3.6 第 3 期验收标准

```bash
pytest -q
# 期望: 所有测试 pass，含数据库 migration 迁移测试

# 启动
start_anp.bat
# 等托盘变绿
```

**手动验收清单**：

- [ ] 总览 Dashboard 顶部出现 4 张卡片
- [ ] 卡片每 5 秒刷新一次（F12 看 Network 有 `/api/monitor/cards`）
- [ ] 调度器关闭时【下次运行】卡片变红
- [ ] 模拟连续 2 次发布失败 → 【最近结果】变红
- [ ] 模拟登录态剩 3 天 → 【登录态】变黄
- [ ] 跑几次发布让本月预算超 80% → 【本月预算】变黄
- [ ] 4 张卡片点击都能跳到正确位置
- [ ] dry-run 模式生成的作品在审核列表里有 📋 标记 + 浅蓝边框
- [ ] critical 通知出现红色顶部横幅，不会自动消失
- [ ] warning 通知右下角 toast 10 秒后淡出
- [ ] info 通知右下角 toast 3 秒淡出
- [ ] 数据库迁移：旧的 `stories` 表自动加 `is_dry_run` 列，旧数据值为 0

## 3.7 第 3 期注意事项

1. **数据库迁移要幂等**：检查列存在再 ALTER，不存在才加
2. **5 秒轮询不影响性能**：`/api/monitor/cards` 必须在 50ms 内返回
3. **预算计算**：从 SQLite 的 `api_calls` 表（如不存在则建）累加 token 使用量 × 模型单价
4. **`next_run_time` 取自 APScheduler**：`scheduler.get_job(id).next_run_time`，没有调度器则返回 null
5. **登录态有效期**：复用第 1 期的 `login_state_validity()`，加缓存（每 60 秒一次，避免读文件过频）
6. **演练数据视觉**：CSS-only 实现，不用 JS 判断
7. **通知不打扰时段**：服务端判断而非客户端，避免规避

---

# 📋 全局：环境与运行命令速查

## 启动开发环境

```bash
cd D:\Development_alma\anp
.\.venv\Scripts\activate
pip install -r requirements.txt

# 第 1 期开发期间：直接启 uvicorn
python -m review_queue.human_review --port 18000

# 第 2 期完成后：启托盘（生产模式）
start_anp.bat
```

## 跑测试

```bash
pytest -q                                    # 全部测试
pytest -q tests/test_yaml_writer.py          # 单文件
pytest -q --cov=review_queue --cov-report=term-missing  # 覆盖率
```

## 重置到干净状态（开发调试用）

```bash
# 注意：会清掉所有生成的小说
rm -r data/anp.sqlite3 data/browser/ logs/
python -c "from review_queue.db import initialize_database; from config_loader import load_from_environment; initialize_database(load_from_environment())"
```

## Git 提交规范

每期完成后：

```bash
git add -A
git commit -m "feat(visual): Phase 1 - settings tab and YAML/env writers"
git commit -m "feat(visual): Phase 2 - tray app and Windows auto-start"
git commit -m "feat(visual): Phase 3 - dashboard cards and notification refinement"
```

---

# 🚦 新会话快速开工 Checklist

新对话开始时，按这个顺序：

1. **读这份文档**：`docs/VISUAL_REBUILD_PLAN.md`（你正在看）
2. **读项目背景**：`README.md`（10 分钟）
3. **快速浏览关键文件**：
   - `config_loader.py`（配置加载逻辑）
   - `review_queue/human_review.py`（现有 API 路由）
   - `review_queue/dashboard_assets.py`（现有 UI 模板）
   - `scheduler.py`（APScheduler 入口）
   - `config.yaml`（配置字段）
4. **跑一次现状测试确认环境 OK**：`pytest -q`（应全 pass）
5. **从第 1 期开始**：创建 8-10 个细分 task（可参考 §1.1 文件清单），逐个写
6. **每完成一个文件就跑测试**，不要堆到最后
7. **每完成一期就让用户验收**（按 §1.6 / §2.9 / §3.6 的清单）

---

# ⚠️ 高优先级红线（违反会导致返工）

1. ❌ **不要用 `yaml.safe_dump` 写 `config.yaml`**——会丢注释和顺序，必须用 `ruamel.yaml`
2. ❌ **不要用 `dotenv.set_key` 写 `.env`**——会丢注释和顺序，必须自己写解析器
3. ❌ **不要在 FastAPI handler 里直接调 `playwright.sync_api`**——必须 subprocess
4. ❌ **不要返回完整 API key**——任何 GET 端点的 secret 字段必须遮罩
5. ❌ **不要重写 `config_loader.py`**——它是只读且工作正常的
6. ❌ **不要破坏现有的 5 个 tab**——只新增⚙️ 设置 tab
7. ❌ **不要删除任何 CLI 入口**（`cli/*.py`）——它们是 CI 和高级用户的备份
8. ❌ **不要把 UI 监听改成 0.0.0.0**——必须保持 127.0.0.1
9. ❌ **不要把番茄账号密码做表单自动登录**——违反 README §8 风控原则
10. ❌ **不要在第 1 期就动托盘和自启**——分期是为了风险隔离，不要混期

---

# 🎯 整体完成判定

3 期全部交付后，达成的状态：

| 维度 | 之前 | 之后 |
|---|---|---|
| 改 config.yaml | 必须打开文件 | UI 内全部可改 |
| 改 .env | 必须打开文件 | UI 内全部可改 |
| 切 dry-run / 真实 | 改 yaml | 顶部横幅一键切 |
| 调整 cron | 学 cron 语法 | 时间选择器 |
| 番茄登录 | 命令行 playwright codegen | 点按钮弹浏览器 |
| 启动软件 | 双击 .bat（黑窗口） | 双击 .bat（无窗口，托盘出现） |
| 关掉软件 | Ctrl+C | 右键托盘退出 |
| 后台常驻 | 关窗口=死 | 托盘常驻 |
| 开机自启 | 手动配 Task Scheduler | UI 单开关 |
| 出事知道 | 翻日志 | Windows 桌面通知 |
| 状态总览 | 队列计数 | 4 张卡片 + 通知 |

**最终一句话验收**：把项目交给一个完全不懂编程的家人，他能正常使用 ✅

---

> 文档版本：v1.0 · 2026-04-29
> 决策树通过 grill-me 技能交互式确认，共 12 项关键决策全部敲定
> 维护人：项目所有者 + 后续接手的 Claude 实例
