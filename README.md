# 外脑 · Cordis Brain

把记忆、检索、提醒交给系统，人只负责**思考、决策、执行、反馈**。

基于 `cordis`「一切皆插件」内核构建的个人外脑。**本地优先、内网可用、零依赖可运行**：
核心只用 Python 标准库（`sqlite3`+FTS5、`http.server`、`zoneinfo`、`tomllib`），
可选依赖装上自动增强，没装自动降级。

> 已跑通并逐条 HTTP 验证：**捕获 → 自动解析 → 入库 → 混合检索 → 今日视图 → 问答 →
> 提醒/通知 → 每日/每周回顾 → 实体图谱 → 主动建议 → 拆子任务/关联项目 → 备份/迁移 → 文件/语音捕获**。

前端是**零构建、零 CDN** 的单页应用（`web/` 下三个文件），手机优先：
手机用底部 tabbar、桌面用顶部导航，深浅主题随手切。同一套前端既能用浏览器打开，
也能原样打进 **Android APK**（见下文「打包成 Android APK」）。

---

## 快速开始

```bash
# 依赖全部可选（内网 pip 源可装；不装也能跑）
pip install -r requirements.txt     # 轻量：jieba(分词) / requests(HTTP)
                                     # 按需：pypdf / python-docx / openai-whisper（文件/语音）

# 起服务（默认 127.0.0.1:8787）
python server.py

# 浏览器打开
#   http://127.0.0.1:8787/
```

测试：
```bash
python tests/test_smoke.py           # 冒烟（进程内）
python tests/test_deploy.py          # 部署测试：全 API + 前端 + 并发 + 持久化（独立临时数据目录）
python server.py --selftest          # 更详细的端到端打印
```

---

## 界面

前端是纯原生 JS 的**零构建**单页应用（`web/index.html` + `web/app.css` + `web/app.js`），
不依赖任何 CDN 或打包工具，断网、离线、内网都能直接用。

| 能力 | 说明 |
|---|---|
| 响应式 | 内容铺满窗口宽度（左右留白 12–36px 随宽度自适应）：≤720px 底部 tabbar + 单列；再宽则今日分区 / 捕获页 / 数据页按可用宽度自动分栏（1→2→3→4… 列） |
| 主题 | 深 / 浅两套，跟随系统或手动切换（记在 `localStorage`） |
| 页面 | 今日 / 捕获 / 搜索 / 问答 / 提醒 / 回顾 / 图谱 / 建议 / 数据 |
| 捕获 | 文本框、📎 文件、🎤 语音；语音在 APK 内优先走 Android 原生录音（见 `android/README.md`） |
| 交互 | 按钮默认中性色、**鼠标悬停变蓝**，触屏按压变蓝；确认框 / 输入框自绘（WebView 里原生 `confirm()` 不可靠） |
| 账号 | 首次进入注册即可，单机多用户、数据按用户隔离；Bearer token 存在本地 |

界面截图见 `android/screenshots/`（桌面、手机深色、手机浅色）。

---

## 打包成 Android APK

`android/` 下用 **buildozer + python-for-android**（`webview` bootstrap）把整个服务打进一个
自包含 APK：包内起 `127.0.0.1:8787` 的本地 Python 服务，WebView 显示同一套前端，
离线可用、无需外部服务器。

```powershell
# 项目根目录，需要 Docker Desktop
powershell -ExecutionPolicy Bypass -File android\build_apk.ps1
```

产物：`android/dist/cordisbrain-0.1.0-arm64-v8a-debug.apk`（debug 签名，可直接安装）。

前置条件、缓存卷、`RECORD_AUDIO` 权限、WebView 补丁、原生录音桥 `CordisAudio` 与排错，
详见 **[`android/README.md`](android/README.md)**。

---

## 打包成 Windows 桌面客户端

`desktop/` 下是一个 **pywebview** 外壳（自带 Qt WebEngine/Chromium，无 Qt 时回退系统
WebView2），只负责打开界面，服务仍需另外跑（本机 `python server.py`，或服务器上的同款服务）。

```powershell
cd desktop
python -m PyInstaller --noconfirm cordis-desktop.spec   # 需要 pyinstaller + pywebview + PySide6
```

产物：`desktop/dist/cordis-desktop/cordis-desktop.exe`（整个文件夹拷走即可用）。
窗口默认 1280×860、最小 920×600；网页界面会跟随窗口宽度自适应。
更细的使用说明见 `desktop/使用说明.txt`。

---

## 设计原则

| 原则 | 落地 |
|---|---|
| 捕获无摩擦 | 文本框 10 秒记下；或 📎 文件 / 🎤 语音，后端解析 |
| 整理自动化 | 自动判类型 / 标题 / 摘要 / 标签 / 时间 / 项目 / 优先级 |
| 检索语义化 | FTS5(BM25) + 中文 2-gram + LIKE 兜底 + RRF 融合，中文短词可召回 |
| 提醒情境化 | 时间/周期/稍后提醒 → 本地通知 outbox（浏览器轮询 + 铃铛） |
| 主动替你把关 | 扫描逾期/停滞/重复/重要未排期 → 一键改期/完成/合并 |
| 回顾周期化 | 每日/每周自动回顾（完成/遗漏/建议） |
| 可迁移 | SQLite 快照备份/恢复 + JSON 导出/导入（换机/外部工具） |

人只负责：输入、决策、执行、反馈。
系统负责：记忆、连接、检索、提醒、复盘、建议。

---

## 架构（一切皆插件）

```
cordis 内核（Context + Service + EventEmitter + Fiber）
  └── 每个能力 = 一个插件，ctx.xxx 取服务，事件流串联
        config     配置（TOML + BYOK 环境变量）
        db         SQLite + FTS5（数据模型，老库自动迁移）
        ai         AI 网关（heuristic 默认 / openai / ollama 可切换）
        entities   实体图谱（person/project/event/concept + 关系）
        reminder   提醒调度（时间/周期/稍后，幂等触发）
        notify     通知 outbox（监听 reminder.due）
        review     每日/每周回顾
        suggestions 主动建议（逾期/停滞/重复/重要未排期）
        workflow   工作流（拆子任务 / 自动关联项目）
        backup     备份/恢复/JSON 迁移
        io         文件/语音解析（PDF/DOCX/HTML + 可选 ASR）
        capture    捕获 → emit capture.received → 解析入库 → emit item.stored
        search     混合检索 + 问答
        today      今日视图
        http       REST API + 静态前端（stdlib ThreadingHTTPServer）
```

事件流：`capture.received → 解析 → item.stored`（同时触发 实体抽取 / 自动提醒 / 自动关联项目 / 自动拆子任务）。

**启动必须在 asyncio 里 `await ctx.plugin(...)`**（cordis 的 `plugin()` 是协程）。

---

## 数据模型

`items`（所有捕获，含 `parent_id` 支持子任务）、`projects`、`tags`/`item_tags`、
`entities`、`relations`、`reminders`、`reviews`、`notifications`、`embeddings`、
`capture_log`（原始不可变日志）、`audit_logs`、`kv`。FTS5 外部内容表 `fts_items` 由触发器同步。

**向前兼容迁移**：启动时 `db._migrate()` 用 `PRAGMA table_info` 检查并 `ALTER TABLE` 补新列（如 `parent_id`），老库无损升级。

**可迁移**：SQLite 快照备份（`data/backups/`）+ JSON 导出/导入（按原文去重）。

---

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康 / 记录数 / AI 状态 |
| GET | `/api/config` | 生效配置（脱敏） |
| GET/PUT | `/api/settings` | 读写全局设置（AI 地址/模型/密钥等，仅管理员即首个注册用户） |
| POST | `/api/auth/register` | 注册 `{"username","password"}` |
| POST | `/api/auth/login` | 登录，返回 Bearer token |
| GET | `/api/auth/me` | 当前用户 |
| POST | `/api/auth/logout` | 退出 |
| POST | `/api/capture` | 快速捕获 `{"text","source","meta"}` → 自动解析入库 |
| POST | `/api/capture/upload` | 文件/语音捕获 `{"filename","content_base64","source"}` → 后端解析 |
| GET | `/api/items` | 列表（可按 type/project/tag/status/日期过滤） |
| GET/PATCH | `/api/items/{id}` | 读 / 改（标题、类型、due、优先级、tags…） |
| DELETE | `/api/items/{id}` | 删除（级联子任务与提醒，不可恢复） |
| POST | `/api/items/{id}/complete` | 标记完成 |
| POST | `/api/items/{id}/reparse` | 重新解析 |
| POST | `/api/items/{id}/split` | 拆子任务 `{"tasks":[...]}`（或逗号分隔字符串） |
| GET | `/api/items/{id}/children` | 子任务列表 |
| GET | `/api/search?q=&type=&tag=&…` | 混合搜索 |
| GET | `/api/ask?q=` | 自然语言问答（带来源） |
| GET | `/api/today?date=` | 今日视图（逾期/今日/7天内/等待/新增） |
| GET | `/api/projects` | 项目列表 |
| GET | `/api/reminders` | 提醒列表 |
| POST | `/api/reminders` | 建提醒 `{"item_id","due_at","kind"}` |
| PATCH | `/api/reminders/{id}` | `{"action":"snooze|cancel|fire","minutes"}` |
| GET | `/api/notifications` | 通知 outbox |
| POST | `/api/notifications/{id}/read` | 标已读 |
| GET/POST | `/api/reviews/daily` | 每日回顾（POST 生成） |
| GET/POST | `/api/reviews/weekly` | 每周回顾（POST 生成） |
| GET | `/api/entities` | 实体列表 |
| GET | `/api/graph` | 实体图谱 |
| GET | `/api/suggestions` | 主动建议 |
| POST | `/api/suggestions/action` | 执行建议 `{"action":"complete|reschedule_today|reschedule_next_day|delete|merge","item_ids":[…]}` |
| GET | `/api/backup` | 备份列表 |
| POST | `/api/backup` | 创建快照备份 `{"label"}` |
| GET | `/api/backup/{name}/download` | 下载备份（sqlite 文件） |
| POST | `/api/backup/{name}/restore` | 恢复到该备份（先自动再备份一次） |
| GET | `/api/export.json` | 导出 JSON（迁移/外部工具） |
| POST | `/api/import.json` | 导入 JSON（按原文去重） |

---

## 自动解析（离线 heuristic）

从一句自然语言里抽出结构化 JSON：
```
type / title / summary / tags / people / projects / project /
due_at / priority / context / actions / confidence
```
- 类型：任务 / 日程 / 灵感 / 资料 / 笔记（关键词打分 + URL/时间修正）
- 时间：中文相对时间（今天/明天/后天/下周X/X月X日/下午3点…，含上下午默认时段）
- 优先级：紧急/重要/有空 → P0~P3
- **无 LLM 也能用**；`config.toml` 把 `ai.provider` 切成 `openai`/`ollama` 即用更强解析（BYOK / 本地 Ollama）

---

## 配置（config.toml）

- `server.host/port/data_dir/web_dir`；可选 HTTPS：`https_port` + `tls_cert` / `tls_key`（证书不存在或无 `_ssl` 时自动跳过）
- `time.timezone`、`reminders.poll_interval`
- `db.path`（留空 = `data/brain.db`）
- `search.use_fts5` = `auto|on|off`、`search.semantic` = `auto|off`
- `capture.provider` = `auto`（LLM 优先，超时/不可用回退规则）/ `heuristic`（纯规则）
- `ai.provider` = `heuristic`（默认）/ `openai` / `ollama`
- `ai.openai.{base_url, api_key, model, embed_model}`：默认填的是**公网通用地址与模型**
  （`https://api.openai.com/v1`、`gpt-4o-mini`、`text-embedding-3-small`），`api_key` 留空；
  也可以不写配置、直接用环境变量 `OPENAI_API_KEY`（BYOK）
- `ai.ollama.{base_url, model, embed_model}`：本地 Ollama，例如 `http://127.0.0.1:11434`

> 默认 `ai.provider = "heuristic"`：零依赖、离线、毫秒级响应。只有确实要用云端解析时才切到
> `openai`——注意：环境里存在 `OPENAI_API_KEY` 时 `openai` 会被视为可用，捕获会先请求公网；
> 网络不通会按超时回退到规则解析，离线场景建议保持 `heuristic`。

---

## 目录结构

```
cordis/            插件内核（已有，未改动）
brain/
  config.py        配置插件
  db.py            SQLite + FTS5 存储（含 _migrate 前向兼容迁移）
  ai.py            AI 网关 + heuristic 解析 + 离线 embedding
  textproc.py      中文 2-gram 分词（jieba 可选增强）
  timeutil.py      中文相对时间解析
  pipeline.py      捕获/解析/检索/今日/问答
  remind.py        提醒调度
  notify.py        通知 outbox
  review.py        每日/每周回顾
  entities.py      实体图谱
  suggestions.py   主动建议
  workflow.py      工作流（拆子任务/关联项目）
  sync.py          备份/恢复/JSON 迁移
  io.py            文件/语音解析
  http.py          REST API + 静态服务
  util.py          工具
web/
  index.html       单页前端骨架（今日/捕获/搜索/问答/提醒/回顾/图谱/建议/数据）
  app.css          样式：深浅主题、响应式布局、按钮悬停态
  app.js           前端逻辑：API 调用、渲染、录音（原生桥 + getUserMedia 降级）
android/           APK 打包（buildozer + p4a，详见 android/README.md）
desktop/           Windows 桌面壳（pywebview，可选）
server.py          入口（asyncio 启动 + --selftest）
config.toml        配置
tests/             冒烟 + 部署测试
data/              运行时数据（SQLite、备份、上传、TLS 证书；已加入 .gitignore）
```

> 仓库根目录的 `.gitignore` 已排除运行时与构建产物：`data/`（SQLite/WAL、备份、上传、TLS 证书）、
> `__pycache__/`、`*.log`、`android/dist/`、`*.apk`、`desktop/dist/` 等；
> 源码、`config.toml`、`android/screenshots/` 仍照常入库。

---

## 迭代路线

已完成：P0 捕获/检索/问答 → P1 提醒/回顾/图谱 → **P2 主动建议/备份迁移/工作流/文件语音捕获**
→ **P2.5 手机优先前端（深浅主题 / 底部 tabbar / 按钮悬停）与 Android APK**。

下一步（P3，候选）：
1. **语音转写落地**：链路已通（APK 内走原生 `MediaRecorder`，桌面走 `getUserMedia`，录完统一上传）；
   接一个 ASR（`openai-whisper` / `vosk` / OpenAI 兼容接口）后即可自动转文字，
   未配置时只存音频并在条目上记「待转写」。
2. **真·多端增量同步**：目前是本地优先的快照备份 + JSON 迁移（换机/外部工具够用）；跨设备实时同步需引入同步基础设施。
3. **提醒推送**：本地通知 outbox 已就绪，可加系统托盘/声音提示（当前为浏览器轮询 + 铃铛）。
4. **习惯/偏好学习**：基于 `capture_log` + `kv` 学你的常见时间/项目，解析更懂你。

风险对策：LLM 解析错误→置信度 + 可编辑 + 可重解析；提醒幂等去重；隐私→本地优先 + 可导出/可备份。
