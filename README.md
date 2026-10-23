# 外脑 · Cordis Brain

[中文](README.md) · [English](README.en.md)

把记忆、检索、提醒交给系统，人只负责**思考、决策、执行、反馈**。

基于 `cordis`「一切皆插件」内核构建的个人外脑。**本地优先、内网可用、零依赖可运行**：
核心只用 Python 标准库（`sqlite3`+FTS5、`http.server`、`zoneinfo`、`tomllib`），
可选依赖装上自动增强，没装自动降级。

> 已跑通并逐条 HTTP 验证：**捕获 → 规则先落库 → 后台 LLM 细分意图 → 混合检索 → 今日视图 →
> 问答 → 提醒/通知（声音 + 系统通知）→ 每日/每周回顾 → 实体图谱 → 主动建议 →
> 拆子任务/关联项目 → 备份/迁移 → 文件/语音捕获（语音可接 OpenAI 兼容 ASR）→ 习惯画像**。

前端是**零构建、零 CDN** 的单页应用（`web/` 下三个文件），手机优先：
手机用底部 tabbar、桌面用顶部导航，深浅主题随手切。同一套前端既能用浏览器打开，
也能原样打进 **Android APK**（见下文「打包成 Android APK」），或塞进 Windows 桌面客户端。

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
python tests/test_asr_habits.py      # 语音转写（本地假接口）+ 习惯画像 / 自动关联 / 老库 kv 迁移
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
| 页面 | 今日 / 捕获 / 问答 / 搜索 / 提醒 / 回顾 / 图谱 / 建议 / 数据（手机底栏：今日 / 捕获 / 问答 / 复盘 / 更多） |
| 今日 | 顶部的四个大数字（逾期 / 今天 / 7 天内 / 今日新增）**都可以点开**，直接看这一类的条目清单 |
| 复盘 | 数字（新增 / 完成 / 逾期 / 今日到期 / 未结）同样**可以点开**看清单；「AI 润色」失败会写明原因并可重试，绝不把模板总结冒充 AI 结果（✨ 徽章只标记真润色） |
| 捕获 | 文本框、📎 文件、🎤 语音；记下即出结果，AI 细分意图在后台跑完再刷新卡片（见「异步意图解析」） |
| 语音 | 录完自动转写：可接任意 OpenAI 兼容 ASR 接口，也可用本机 whisper / vosk；没配就只存音频并标记「待转写」 |
| 提醒送达 | 应用内横幅 + **提示音**（WebAudio 合成，零素材、离线可用），浏览器/桌面还会发**系统通知**；通知面板里可分别开关 |
| 导出/备份 | 「数据」页导出 JSON、下载备份都会直接落到本机下载目录，不跳浏览器、不打断页面 |
| 交互 | 按钮默认中性色、**鼠标悬停变蓝**，触屏按压变蓝；确认框 / 输入框自绘（WebView 里原生 `confirm()` 不可靠） |
| 账号 | 首次进入注册即可，单机多用户、数据按用户隔离；Bearer token 存在本地 |

界面截图见 `android/screenshots/`（桌面、手机深色、手机浅色）。

---

## 异步意图解析（记下 / 捕获默认走 LLM）

「记下」「捕获」默认就用 LLM 分析意图，配置与「问答」共用同一套 `[ai]`，但**不会阻塞输入**：

```
POST /api/capture
  ① 规则解析（毫秒级）→ 立即入库 → 返回 {item, refining: true}
       · 类型/时间/优先级先按中文规则给出，卡片立刻可见、可编辑
  ② 后台线程调 LLM 重解析意图（标题/摘要/标签/人物/项目/动作/时间）
       · 成功 → 覆盖条目字段 + emit item.updated
       · 前端轮询条目，卡片从「AI 解析中…」变为「AI 解析」并 toast 提示
       · 超时/断网/未配置 → 保留规则结果，回执停止转圈，给出「重试 AI」按钮
```

- `capture.provider = "auto"`（默认）= LLM 优先、失败回退规则；`"heuristic"` = 纯规则离线。
- 等待不会无限转圈：`GET /api/items/{id}` 带 `refining` 实时状态，后台线程一结束前端就收尾；
  失败原因写进 `meta.refine = {state, error}`，回执显示 + 「重试 AI」走 `POST /api/items/{id}/reparse`。
- 解析来源在条目上落库为 `parse_provider`（`heuristic` / `openai` / `ollama`），详情页会显示。
- 后台线程会显式带上发起请求的用户，多用户下不会串数据。
- 环境变量 `OPENAI_API_KEY` **只在 `base_url` 指向官方 `https://api.openai.com` 时**才注入，
  避免误发给第三方网关导致 401（第三方网关用自己的 `api_key`）。

### HTTPS 证书（Android 必读）

APK 里的 Python 只有 OpenSSL、**没有系统 CA 库**，直接访问 https 会报
`CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`（Windows 上正常，手机上一律失败）。
所以仓库自带一份 Mozilla CA 包 `certs/cacert.pem`，`brain/tls.py` 把它接给所有出站 HTTPS
（AI 解析 / AI 润色 / 远程 ASR）：

- 仍然**完整校验证书**，没有用「不校验」绕过——API Key 走的就是这条链路；
- 想换 CA（比如内网自签网关）时设置环境变量 `CORDIS_CA_BUNDLE=/path/to/ca.pem` 即可；
- 打包脚本会把 `certs/` 一起放进 APK，并额外生成 `brain/ca_data.py`（zlib+base64 内嵌同一份 CA）做兜底，
  即使数据文件被 p4a 过滤掉也能校验证书；删掉 `certs/` 手机端 AI 会全部失败。

---

## 语音转写（ASR）

录音链路（APK 内原生 `MediaRecorder`、桌面/浏览器 `getUserMedia`）早已跑通，转写按下面顺序自动挑：

```
POST /api/capture/upload（source=voice）
  ① OpenAI 兼容接口：POST {asr.base_url}/audio/transcriptions（multipart，标准库实现）
       · 任何兼容网关都行（官方 OpenAI、faster-whisper / vLLM 自建、内网代理）
       · asr.api_key 留空时：与 [ai.openai] 同源则自动复用其 Key；官方端点读 OPENAI_API_KEY
  ② 本机 whisper（装 openai-whisper 即可）
  ③ 本机 vosk（需 asr.vosk_model 模型目录；只吃 wav 16k 单声道）
  ④ 都没有/失败 → 音频存 data/uploads/，条目记「语音已保存（待转写）」，接口回 asr_error 说明原因
```

- 设置页只多了一组「语音转写」字段：`provider`（auto/openai/whisper/vosk/off）、`base_url`、
  `api_key`、`model`（默认 `whisper-1`）、`language`、`vosk_model`；保存即时生效。
- 转写成功会照常走捕获流水线（类型/时间/tags 由规则+LLM 解析），前端 toast 显示命中的引擎；
  失败时 toast 直接给出原因（如「未配置语音转写」「远程接口失败：…」），不再静默。
- `/api/health` 与 `/api/habits` 里带 `asr` 状态（是否配了远程、本机有哪些模块、是否可用）。

---

## 提醒送达：声音 + 系统通知

提醒调度（`reminders` 表 + outbox）没变，只是把「到点提醒」送达方式补齐了：

| 通道 | 实现 | 备注 |
|---|---|---|
| 应用内横幅 | 通知轮询发现新通知即弹出，12 秒自动消失 | 各端都有，最可靠 |
| 提示音 | WebAudio 合成两音「叮咚」（880 Hz + 1175 Hz） | 零素材、离线可用，安卓 WebView / 桌面 Qt 都支持 |
| 系统通知 | 浏览器 Notification API，点通知可聚焦窗口 | 安卓 WebView 多数不支持，自动跳过；需在通知面板点一次「系统通知」授权 |
| 标签页角标 | 页面不可见时标题变 `(3) 外脑 · Cordis Brain` | 切回来即恢复 |

两个开关都在 🔔 通知面板底部的「提醒方式」里，按设备存在 `localStorage`，随时可关。

---

## 习惯画像

`brain/habits.py` 只读自己的捕获记录，统计近 120 天的**常用时段 / 常用项目 / 常用标签 / 类型分布**
（外加从「标签 × 项目」共现里学规则），结果缓存在 `kv`（30 分钟，可手动重算）。目前落地四处：

1. **解析更懂你**：只写了日期、没写钟点时（如「明天交季度报告」），默认时间用你的常用小时补全，
   而不是一律 9:00。显式写了「下午3点 / 15:30 / 上午」的绝不覆盖；样本少于 3 条时保持 9:00。
2. **常记项目自动关联**：没在文本里点名项目时，按学到的规则挂上项目——
   ① 「标签 → 项目」（某标签至少 3 次、且 ≥60% 都落在同一个项目，如「工作」→「星火计划」）；
   ② 兜底「主导项目」（**≥8 条**且 **≥60%** 的项目占比）。
   挂上后条目 `meta.auto_project` 里留痕（`source` / `reason` / `confidence`），捕获回执与详情里都能看到
   「📁 自动关联『X』· 常用标签：工作」，不会偷偷改你的数据。保守起见：已挂项目不动、项目被删不关联、
   设置里可整组关掉（`habits.auto_project = auto | tags | off`）。
3. **建议排序加权**：建议列表内部按 `0.5 × 习惯分 + 0.5 × 紧迫度` 排序——习惯分 = 常记项目 0.6 +
   常用标签 0.4。逾期仍按逾期天数、重要未排期仍按 P0/P1 打底，只是把「你真正在忙的项目」往上提。
   可关：`habits.suggest_boost = auto | off`。
4. **数据页「习惯画像」卡片**：常用时段 / 项目 / 标签 / 类型，外加「学会了哪些『标签→项目』规则」，
   带「重新统计」按钮。

```
GET  /api/habits             → {profile, asr, modes}
GET  /api/habits?refresh=1   → 强制重算
POST /api/habits/rebuild     → 同上（幂等）
```

统计严格按 `user_id` 过滤，多用户不会串；习惯只影响**顺序与默认值**，从不删除或改写你的内容。

---

## 打包成 Android APK

`android/` 下用 **buildozer + python-for-android**（`webview` bootstrap）把整个服务打进一个
自包含 APK：包内起 `127.0.0.1:8787` 的本地 Python 服务，WebView 显示同一套前端，
离线可用、无需外部服务器。

```powershell
# 项目根目录，需要 Docker Desktop（脚本会尝试自动拉起）
powershell -ExecutionPolicy Bypass -File android\build_apk.ps1
```

产物：`android/dist/cordisbrain-0.1.0-arm64-v8a-debug.apk`（debug 签名，可直接安装），
同一份也会复制成 `android/dist/cordisbrain-0.1.0.apk` 方便识别。

安装：`adb install -r android\dist\cordisbrain-0.1.0.apk`。

APK 内的文件保存走原生桥 `CordisFiles`（写系统「下载」目录，**不需要存储权限**）——
旧的 `window.location` 下载会被系统浏览器接管，浏览器连 `127.0.0.1` 被拒，也就是
「导出报错 / 错误码 102」。

前置条件、缓存卷、`RECORD_AUDIO` 权限、WebView 补丁（`CORDIS_PATCH_V5`）、原生录音桥
`CordisAudio`、文件保存桥与排错，详见 **[`android/README.md`](android/README.md)**。

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
导出 JSON / 下载备份由客户端自己落盘到 `%USERPROFILE%\Downloads`（`window.pywebview.api.save_file`），
不弹系统下载框。更细的使用说明见 `desktop/使用说明.txt`。

---

## 设计原则

| 原则 | 落地 |
|---|---|
| 捕获无摩擦 | 文本框 10 秒记下；或 📎 文件 / 🎤 语音，后端解析 |
| 整理自动化 | 规则毫秒出结果 + 后台 LLM 细分意图（标题/摘要/标签/时间/项目/优先级/动作） |
| 检索语义化 | FTS5(BM25) + 中文 2-gram + LIKE 兜底 + RRF 融合，中文短词可召回 |
| 提醒情境化 | 时间/周期/稍后提醒 → 本地通知 outbox，横幅 + 提示音 + 系统通知 |
| 越用越顺手 | 习惯画像学你的常用时段/项目/标签：只写日期时补到常用时间、常记项目自动关联、建议排序加权 |
| 主动替你把关 | 扫描逾期/停滞/重复/重要未排期 → 一键改期/完成/合并 |
| 回顾周期化 | 每日/每周自动回顾（完成/遗漏/建议），同名日期重复生成幂等 |
| 可迁移 | SQLite 快照备份/恢复 + JSON 导出/导入（换机/外部工具，保真到项目与父子关系） |

人只负责：输入、决策、执行、反馈。
系统负责：记忆、连接、检索、提醒、复盘、建议。

---

## 架构（一切皆插件）

```
cordis 内核（Context + Service + EventEmitter + Fiber）
  └── 每个能力 = 一个插件，ctx.xxx 取服务，事件流串联
        config     配置（TOML + BYOK 环境变量）
        db         SQLite + FTS5（数据模型，老库自动迁移）
        ai         AI 网关（默认 Kilo 免费网关 / openai / ollama / heuristic 可切换）
        entities   实体图谱（person/project/event/concept + 关系）
        reminder   提醒调度（时间/周期/稍后，幂等触发）
        notify     通知 outbox（监听 reminder.due）
        review     每日/每周回顾
        suggestions 主动建议（逾期/停滞/重复/重要未排期）
        workflow   工作流（拆子任务 / 自动关联项目）
        habits     习惯画像（常用时段/项目/标签；补全默认时间 + 自动关联项目 + 建议加权）
        backup     备份/恢复/JSON 迁移
        io         文件/语音解析（PDF/DOCX/HTML + ASR：远程兼容接口 / whisper / vosk）
        capture    捕获 → emit capture.received → 规则落库 → 后台 LLM 细分意图
        search     混合检索 + 问答
        today      今日视图
        http       REST API + 静态前端（stdlib ThreadingHTTPServer）
```

事件流：`capture.received → 解析 → item.stored`（同时触发 实体抽取 / 自动提醒 / 自动关联项目 / 自动拆子任务）；
后台细分意图完成后再 emit `item.updated`。

**启动必须在 asyncio 里 `await ctx.plugin(...)`**（cordis 的 `plugin()` 是协程）。

---

## 数据模型

`items`（所有捕获，含 `parent_id` 支持子任务）、`projects`、`tags`/`item_tags`、
`entities`、`relations`、`reminders`、`reviews`、`notifications`、`embeddings`、
`capture_log`（原始不可变日志）、`audit_logs`、`kv`。FTS5 外部内容表 `fts_items` 由触发器同步。

**向前兼容迁移**：启动时 `db._migrate()` 用 `PRAGMA table_info` 检查并 `ALTER TABLE` 补新列（如 `parent_id`），
老库无损升级。老库的 `reviews` 表若带表级 `UNIQUE(kind, date)`（会让**不同用户**在同一天互相撞约束），
启动时会自动重建该表为只按 `(user_id, kind, date)` 唯一（数据与 id 保留、幂等）；写法是独立
`UNIQUE(kind, date)` 索引的老库则直接 DROP 该索引。老库的 `kv` 表同理：早期是「key 全局唯一」，
多用户写同一个键（如习惯缓存 `habits.profile`）会撞 `UNIQUE constraint failed: kv.key`，启动时
自动重建为 `(user_id, key)` 唯一，老数据原样保留、迁移幂等。

**可迁移**：SQLite 快照备份（`data/backups/`，仅管理员即首个注册用户可操作）+ JSON 导出/导入。
导出/导入的保真度：保留 `created_at` / `updated_at` / `summary` / `meta` / `context`，
项目按名称重新关联，子任务的 `parent_id` 按导出文件里的 id 映射还原；重复条目按原文（`raw`）跳过。

「数据」页的导出 / 下载在各端都直接落到本机「下载」目录：安卓走 `CordisFiles` 原生桥
（MediaStore，无需存储权限），桌面客户端走 `window.pywebview.api.save_file`，普通浏览器走 `<a download>`。

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
| POST | `/api/capture` | 快速捕获 `{"text","source","meta"}` → 立即入库，`refining:true` 表示后台仍在细分意图 |
| POST | `/api/capture/upload` | 文件/语音捕获 `{"filename","content_base64","source"}` → 后端解析；语音回 `asr` / `asr_provider` / `asr_error`（同样带 `refining`） |
| GET | `/api/items` | 列表（可按 type/project/tag/status/日期过滤） |
| GET/PATCH | `/api/items/{id}` | 读 / 改（标题、类型、due、优先级、tags…）；轮询 `parse_provider` 可知细分成败 |
| DELETE | `/api/items/{id}` | 删除（级联子任务与提醒，不可恢复） |
| POST | `/api/items/{id}/complete` | 标记完成 |
| POST | `/api/items/{id}/reparse` | 重新解析 |
| POST | `/api/items/{id}/split` | 拆子任务 `{"tasks":[...]}`（或逗号分隔字符串） |
| GET | `/api/items/{id}/children` | 子任务列表 |
| GET | `/api/search?q=&type=&tag=&…` | 混合搜索 |
| GET | `/api/ask?q=` | 自然语言问答（带来源） |
| GET | `/api/today?date=` | 今日视图（逾期/今日/未来7天/等待/新增；`counts.within7` 含当天） |
| GET | `/api/projects` | 项目列表 |
| GET | `/api/reminders` | 提醒列表 |
| POST | `/api/reminders` | 建提醒 `{"item_id","due_at","kind"}` |
| PATCH | `/api/reminders/{id}` | `{"action":"snooze|cancel|fire","minutes"}` |
| GET | `/api/notifications` | 通知 outbox |
| POST | `/api/notifications/{id}/read` | 标已读 |
| GET/POST | `/api/reviews/daily` | 每日回顾（POST 生成，同用户同日期重复生成为更新） |
| GET/POST | `/api/reviews/weekly` | 每周回顾（同上） |
| GET | `/api/entities` | 实体列表 |
| GET | `/api/graph` | 实体图谱 |
| GET | `/api/suggestions` | 主动建议 |
| POST | `/api/suggestions/action` | 执行建议 `{"action":"complete|reschedule_today|reschedule_next_day|delete|merge","item_ids":[…]}` |
| GET | `/api/backup` | 备份列表（管理员） |
| POST | `/api/backup` | 创建快照备份 `{"label"}`（管理员） |
| GET | `/api/backup/{name}/download` | 下载备份（sqlite 文件） |
| POST | `/api/backup/{name}/restore` | 恢复到该备份（先自动再备份一次） |
| GET | `/api/export.json` | 导出 JSON（迁移/外部工具） |
| POST | `/api/import.json` | 导入 JSON（按原文去重，保留时间戳/项目/父子关系） |
| GET | `/api/habits` | 习惯画像（常用时段/项目/标签/类型 + 「标签→项目」规则）；返回 `profile` / `asr` / `modes`；`?refresh=1` 强制重算 |
| POST | `/api/habits/rebuild` | 重算习惯画像（幂等） |

> `/api/*` 都可用 `Authorization: Bearer <token>` 或 `?token=` 鉴权（后者便于下载链接）。

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
- **无 LLM 也能用**；配上 LLM 时规则负责「秒回」，LLM 负责「更准」，两者叠加不冲突

---

## 配置（config.toml）

- `server.host/port/data_dir/web_dir`；可选 HTTPS：`https_port` + `tls_cert` / `tls_key`（证书不存在或无 `_ssl` 时自动跳过）
- `time.timezone`、`reminders.poll_interval`
- `db.path`（留空 = `data/brain.db`）
- `search.use_fts5` = `auto|on|off`、`search.semantic` = `auto|off`
- `capture.provider` = `auto`（默认：规则先落库 + 后台 LLM 细分意图）/ `heuristic`（纯规则）
- `asr.provider` = `auto`（默认：远程 OpenAI 兼容 → 本机 whisper → 本机 vosk）/ `openai` / `whisper` / `vosk` / `off`
  - `asr.base_url`（如 `https://api.openai.com/v1`、自建 faster-whisper 网关，留空=不启用远程）、
    `asr.api_key`（留空=同源复用 `[ai.openai]` 的 Key）、`asr.model`（默认 `whisper-1`）、
    `asr.language`、`asr.vosk_model`、`asr.timeout`（默认 60s）
- `habits.auto_project` = `auto`（默认：标签规则 → 主导项目兜底）/ `tags`（只用标签规则）/ `off`
- `habits.suggest_boost` = `auto`（默认：建议按习惯加权）/ `off`
- `ai.provider` = `openai`（默认，已接入 Kilo 免费网关）/ `ollama` / `heuristic`
  —— **问答、记下、捕获共用这一套**
- `ai.openai.{base_url, api_key, model, embed_model}`：默认
  `https://api.kilo.ai/api/gateway`、`kilo-auto/free`（**免费模型无需 API Key**）；
  换其他网关时填自己的 `api_key`（也支持环境变量 `OPENAI_API_KEY`，仅对官方端点生效），
  并按需填 `embed_model`（Kilo 网关无向量模型，默认留空 = 仅关键词检索）
- `ai.ollama.{base_url, model, embed_model}`：本地 Ollama，例如 `http://127.0.0.1:11434`

> 默认 `ai.provider = "openai"` 指向 Kilo 免费网关，开箱即用；网络不通/接口失败时会自动
> 回退到规则解析与关键词检索，不会阻塞捕获。纯离线场景可在设置里切回 `heuristic`。
> 免费网关本身响应波动较大（实测 10–45s），所以捕获走异步：先出结果，AI 回来再刷新。

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
  pipeline.py      捕获/解析/检索/今日/问答（含后台 LLM 细分意图）
  remind.py        提醒调度
  notify.py        通知 outbox
  review.py        每日/每周回顾
  entities.py      实体图谱
  suggestions.py   主动建议
  workflow.py      工作流（拆子任务/关联项目）
  habits.py        习惯画像（常用时段/项目/标签 → 默认时间 / 自动关联项目 / 建议加权）
  sync.py          备份/恢复/JSON 迁移（保真导入导出）
  io.py            文件/语音解析（PDF/DOCX/HTML + ASR：远程兼容接口/whisper/vosk）
  http.py          REST API + 静态服务
  tls.py           出站 HTTPS 的 CA 信任链（Android 没有系统 CA，随包 certs/cacert.pem）
  util.py          工具
certs/cacert.pem   Mozilla CA 包（随 APK 打包，保证手机能校验证书）
web/
  index.html       单页前端骨架（今日/捕获/问答/搜索/提醒/回顾/图谱/建议/数据）
  app.css          样式：深浅主题、响应式布局、按钮悬停态
  app.js           前端逻辑：API 调用、渲染、录音（原生桥 + getUserMedia 降级）、文件落盘
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
→ **P2.5 手机优先前端（深浅主题 / 底部 tabbar / 按钮悬停）、Android APK、Windows 桌面客户端**
→ **P2.6 捕获异步 LLM 意图解析、各端导出/备份落盘、导入导出保真、老库复盘约束自动修复**
→ **P3.1 语音转写（OpenAI 兼容 ASR + whisper/vosk 降级）、提醒提示音+系统通知、习惯画像**
→ **P3.2 习惯进阶：常记项目自动关联（标签→项目学习）、建议排序加权**。

下一步（P3，候选）：
1. **真·多端增量同步**：目前是本地优先的快照备份 + JSON 迁移（换机/外部工具够用）；
   跨设备实时同步需要同步基础设施（版本号 / 冲突策略 / 增量日志），建议单独一轮设计。
2. **系统托盘（桌面端）**：现在有提示音 + 系统通知；托盘常驻需要额外依赖（pystray 等），
   会打破「零依赖」，可在打包版里作为可选增强。
3. **习惯再进一步**：按星期/时间段预测「今天该处理什么」，以及把常记项目做成捕获时的默认建议。
4. **ASR 长音频**：目前同步转写并受 `asr.timeout` 限制（默认 60s）；长录音可改异步任务 + 进度提示。

风险对策：LLM 解析错误→置信度 + 可编辑 + 可重解析；提醒幂等去重；习惯只影响顺序/默认值、不删改内容；隐私→本地优先 + 可导出/可备份。
