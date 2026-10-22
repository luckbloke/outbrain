# Cordis Brain · 外脑

[中文](README.md) · [English](README.en.md)

Let the system handle memory, retrieval and reminders — you focus on **thinking, deciding, doing and feeding back**.

A personal "second brain" built on the `cordis` "everything is a plugin" kernel.
**Local-first, LAN-friendly, runnable with zero dependencies**: the core uses only the Python
standard library (`sqlite3` + FTS5, `http.server`, `zoneinfo`, `tomllib`). Optional dependencies
enhance it when installed and degrade gracefully when they are not.

> Implemented and verified over HTTP, endpoint by endpoint: **capture → rule-based store →
> background LLM intent refinement → hybrid search → today view → Q&A → reminders/notifications
> (banner + sound + system notification) → daily & weekly reviews → entity graph → proactive
> suggestions → subtask splitting / project linking → backup & migration → file / voice capture
> (voice can use any OpenAI-compatible ASR) → habit profile**.

The frontend is a **no-build, no-CDN** single-page app (three files under `web/`), mobile-first:
bottom tabbar on phones, top navigation on desktop, dark/light theming on the fly. The same UI runs
in a browser, inside the **Android APK** (see "Building the Android APK") or inside the Windows
desktop shell.

---

## Quick start

```bash
# Every dependency is optional (install from your local pip mirror if needed)
pip install -r requirements.txt     # light: jieba (tokenizer) / requests (HTTP)
                                    # as needed: pypdf / python-docx / openai-whisper (files & voice)

# Start the service (127.0.0.1:8787 by default)
python server.py

# Open in a browser
#   http://127.0.0.1:8787/
```

Tests:
```bash
python tests/test_smoke.py           # smoke test (in-process)
python tests/test_deploy.py          # deployment test: all APIs + frontend + concurrency + persistence (isolated temp data dir)
python tests/test_asr_habits.py      # speech-to-text (local fake endpoint) + habit profile / auto-linking / legacy kv migration
python server.py --selftest          # verbose end-to-end output
```

---

## User interface

The frontend is plain vanilla JS — a **zero-build** SPA (`web/index.html` + `web/app.css` +
`web/app.js`) with no CDN and no bundler. It works offline, on an intranet, or with no network at all.

| Capability | Details |
|---|---|
| Responsive | Content fills the window width (12–36px side padding scales with width): ≤720px gets a bottom tabbar and a single column; wider screens auto-split Today / Capture / Data into 1→2→3→4… columns |
| Theming | Dark and light, follow the system or switch manually (stored in `localStorage`) |
| Views | Today / Capture / Ask / Search / Reminders / Reviews / Graph / Suggestions / Data (mobile tabbar: Today / Capture / Ask / Review / More) |
| Capture | Text box, 📎 file, 🎤 voice; the note appears instantly and the AI refinement updates the card in the background (see "Async intent parsing") |
| Voice | Recordings are transcribed automatically: any OpenAI-compatible ASR endpoint, or local whisper / vosk; with none configured the audio is kept and flagged "to transcribe" |
| Reminder delivery | In-app banner + **sound** (WebAudio-synthesised, no assets, works offline), plus **system notifications** in browsers/desktop; each can be toggled in the notification panel |
| Export & backup | Exporting JSON or downloading a backup on the Data page writes straight to the device's download folder — no browser hand-off, no page reload |
| Interaction | Buttons are neutral by default and **turn blue on hover**; touch devices turn blue on press; dialogs and prompts are hand-drawn (native `confirm()` is unreliable in WebViews) |
| Accounts | Just register on first launch; multiple users per instance with per-user data isolation; the bearer token is stored locally |

Screenshots live in `android/screenshots/` (desktop, mobile dark, mobile light).

---

## Async intent parsing (Capture uses the LLM by default)

"Capture" uses the LLM to analyse intent by default, sharing the same `[ai]` configuration as Q&A —
without ever blocking your typing:

```
POST /api/capture
  1. Rule-based parse (milliseconds) -> stored immediately -> returns {item, refining: true}
       - type/time/priority come from Chinese rule parsing, so the card is visible and editable at once
  2. A background thread asks the LLM to refine the intent (title/summary/tags/people/projects/actions/time)
       - success -> item fields are overwritten and item.updated is emitted
       - the frontend polls the item; the card goes from "AI parsing..." to "AI parsed" with a toast
       - timeout / offline / not configured -> rule result is silently kept, nothing breaks, nothing is lost
```

- `capture.provider = "auto"` (default) = LLM first, fall back to rules; `"heuristic"` = rules only, fully offline.
- The parse source is stored on the item as `parse_provider` (`heuristic` / `openai` / `ollama`) and shown in the detail sheet.
- The background thread carries the requesting user explicitly, so multi-user data never mixes.
- The `OPENAI_API_KEY` environment variable is injected **only when `base_url` points at the official
  `https://api.openai.com`**, so it is never leaked to third-party gateways (use their own `api_key`).

---

## Speech-to-text (ASR)

Recording was already wired (native `MediaRecorder` in the APK, `getUserMedia` on desktop/browser);
transcription now picks the first working path:

```
POST /api/capture/upload (source=voice)
  1. OpenAI-compatible endpoint: POST {asr.base_url}/audio/transcriptions (multipart, stdlib only)
       - any compatible gateway (official OpenAI, self-hosted faster-whisper / vLLM, internal proxy)
       - empty asr.api_key: reused from [ai.openai] when the base_url is the same gateway;
         for the official endpoint OPENAI_API_KEY is read
  2. local whisper (install openai-whisper)
  3. local vosk (needs asr.vosk_model; wav 16k mono only)
  4. none of them / all failed → audio saved under data/uploads/, item flagged
     "saved, to transcribe", and the response carries asr_error explaining why
```

- Settings gains one "语音转写" (speech-to-text) group: `provider` (auto/openai/whisper/vosk/off),
  `base_url`, `api_key`, `model` (default `whisper-1`), `language`, `vosk_model` — effective immediately.
- A successful transcription flows through the normal capture pipeline (type/time/tags from rules + LLM)
  and the toast names the engine used; failures now report the reason instead of staying silent.
- `/api/health` and `/api/habits` expose an `asr` status block (remote configured? local modules? usable?).

---

## Reminder delivery: sound + system notifications

Scheduling (the `reminders` table plus the outbox) is unchanged; delivery got three channels:

| Channel | Implementation | Notes |
|---|---|---|
| In-app banner | Shown when polling finds a new notification, auto-hides after 12s | Available everywhere, most reliable |
| Sound | WebAudio-synthesised two-note chime (880 Hz + 1175 Hz) | No assets, works offline, works in Android WebView and desktop Qt |
| System notification | Browser Notification API; clicking it focuses the window | Most Android WebViews lack it and it is skipped; grant it once via the notification panel |
| Title badge | Hidden tab shows `(3) Cordis Brain` as the title | Restored when you come back |

Both switches live under "提醒方式" at the bottom of the 🔔 notification panel and are stored per device
in `localStorage`.

---

## Habit profile

`brain/habits.py` only reads your own captures and summarises the last 120 days: **usual hours, usual
projects, usual tags, type mix** — plus rules learned from tag × project co-occurrence. The result is
cached in `kv` (30 minutes, refreshable) and used in four places:

1. **Better parsing**: when a note has a date but no clock time (e.g. "submit the report tomorrow"),
   the default hour becomes *your* usual hour instead of always 09:00. Explicit times
   ("3pm", "15:30", "in the morning") are never overridden, and with fewer than 3 samples it stays 09:00.
2. **Auto-linking your usual project**: when the text does not name a project, it links one from what it
   learned — (a) a "tag → project" rule (a tag used at least 3 times with ≥60% of those items in the same
   project, e.g. "work" → "Project Phoenix"), or (b) the dominant project (**≥8 items**, **≥60%** share).
   Every link is recorded in `meta.auto_project` (`source` / `reason` / `confidence`) and shown in the
   capture result and the detail sheet ("📁 auto-linked …"), so nothing happens silently. Guard rails:
   items that already have a project are untouched, deleted projects are skipped, and the whole thing can
   be switched off (`habits.auto_project = auto | tags | off`).
3. **Suggestion ranking**: items inside each suggestion are ordered by `0.5 × habit score + 0.5 × urgency`,
   where the habit score is 0.6 × usual project + 0.4 × usual tag. Overdue items still start from how
   overdue they are, important-but-undated ones from P0/P1 — habit just lifts the projects you really work
   on. Switchable: `habits.suggest_boost = auto | off`.
4. **A "习惯画像" (habit profile) card on the Data page**: usual hours / projects / tags / types plus the
   learned "tag → project" rules, with a "重新统计" (recompute) button.

```
GET  /api/habits             → {profile, asr, modes}
GET  /api/habits?refresh=1   → force a recompute
POST /api/habits/rebuild     → same (idempotent)
```

All statistics are filtered by `user_id`, so multi-user data never mixes; habits only influence **ordering
and defaults** and never delete or rewrite your content.

---

## Building the Android APK

`android/` uses **buildozer + python-for-android** (the `webview` bootstrap) to package the whole
service into one self-contained APK: it starts a local Python server on `127.0.0.1:8787` inside the
app and shows the same frontend in a WebView. Works offline, no external server needed.

```powershell
# From the project root; Docker Desktop is required (the script tries to start it)
powershell -ExecutionPolicy Bypass -File android\build_apk.ps1
```

Artifact: `android/dist/cordisbrain-0.1.0-arm64-v8a-debug.apk` (debug-signed, installable as-is);
the same file is copied to `android/dist/cordisbrain-0.1.0.apk` for convenience.

Install: `adb install -r android\dist\cordisbrain-0.1.0.apk`.

File saving inside the APK goes through the native bridge `CordisFiles` (writes to the system
Downloads folder, **no storage permission needed**). The old `window.location` download was handed to
the system browser, which cannot reach `127.0.0.1` — that was the "export error / error code 102".

Prerequisites, the cache volume, the `RECORD_AUDIO` permission, WebView patches (`CORDIS_PATCH_V5`),
the native recorder bridge `CordisAudio`, the file-save bridge and troubleshooting are documented in
**[`android/README.md`](android/README.md)** (Chinese).

---

## Building the Windows desktop client

`desktop/` is a **pywebview** shell (bundling Qt WebEngine/Chromium, falling back to the system
WebView2 when Qt is unavailable). It only opens the UI — the service still has to run separately
(`python server.py` locally, or the same service on a server).

```powershell
cd desktop
python -m PyInstaller --noconfirm cordis-desktop.spec   # needs pyinstaller + pywebview + PySide6
```

Artifact: `desktop/dist/cordis-desktop/cordis-desktop.exe` (copy the whole folder to use it).
Window defaults to 1280×860, minimum 920×600; the UI adapts to the window width.
Exporting JSON / downloading a backup is written by the client itself into `%USERPROFILE%\Downloads`
(via `window.pywebview.api.save_file`), with no system download dialog.
See `desktop/使用说明.txt` for a more detailed guide (Chinese).

---

## Design principles

| Principle | How it works |
|---|---|
| Frictionless capture | Type a note in 10 seconds; or 📎 file / 🎤 voice for the backend to parse |
| Automated organisation | Rules answer in milliseconds; the LLM refines intent in the background (title/summary/tags/time/project/priority/actions) |
| Semantic retrieval | FTS5 (BM25) + Chinese 2-grams + LIKE fallback + RRF fusion, so short Chinese queries still hit |
| Contextual reminders | Time / recurring / snooze reminders → local notification outbox, banner + sound + system notification |
| Gets better with use | A habit profile learns your usual hours/projects/tags: default time completion, auto-linking your usual project, weighted suggestion ordering |
| Proactive gatekeeping | Scans overdue / stalled / duplicate / important-but-unscheduled items → reschedule, complete or merge in one tap |
| Recurring reviews | Automatic daily and weekly reviews (done/missed/suggestions); regenerating the same date is idempotent |
| Portable | SQLite snapshot backup/restore + JSON export/import (device migration, external tooling) with projects and parent/child links preserved |

You handle: input, decisions, execution, feedback.
The system handles: memory, connections, retrieval, reminders, reviews, suggestions.

---

## Architecture (everything is a plugin)

```
cordis kernel (Context + Service + EventEmitter + Fiber)
  └── every capability = one plugin, services via ctx.xxx, wired through events
        config      configuration (TOML + BYOK environment variables)
        db          SQLite + FTS5 (data model, automatic migration of old databases)
        ai          AI gateway (default: Kilo free gateway / openai / ollama / heuristic)
        entities    entity graph (person/project/event/concept + relations)
        reminder    reminder scheduling (time/recurring/snooze, idempotent firing)
        notify      notification outbox (listens to reminder.due)
        review      daily / weekly reviews
        suggestions proactive suggestions (overdue/stalled/duplicate/important-unscheduled)
        workflow    workflows (split subtasks / auto-link projects)
        habits      habit profile (usual hours/projects/tags; default times, project auto-linking, suggestion weighting)
        backup      backup / restore / JSON migration
        io          file & voice parsing (PDF/DOCX/HTML + ASR: compatible endpoint / whisper / vosk)
        capture     capture → emit capture.received → rule-based store → background LLM refinement
        search      hybrid search + Q&A
        today       today view
        http        REST API + static frontend (stdlib ThreadingHTTPServer)
```

Event flow: `capture.received → parse → item.stored` (which also triggers entity extraction,
automatic reminders, project linking and subtask splitting); the background refinement emits
`item.updated` when it finishes.

**Startup must `await ctx.plugin(...)` inside asyncio** (cordis's `plugin()` is a coroutine).

---

## Data model

`items` (every capture, with `parent_id` for subtasks), `projects`, `tags`/`item_tags`, `entities`,
`relations`, `reminders`, `reviews`, `notifications`, `embeddings`, `capture_log` (immutable raw
log), `audit_logs`, `kv`. The FTS5 external-content table `fts_items` is kept in sync by triggers.

**Forward-compatible migrations**: at startup `db._migrate()` inspects `PRAGMA table_info` and uses
`ALTER TABLE` to add new columns (such as `parent_id`) without touching existing data. If an old
`reviews` table carries a table-level `UNIQUE(kind, date)` constraint — which makes *different users*
collide on the same day — the table is rebuilt at startup to be unique on `(user_id, kind, date)` only
(rows and ids preserved, idempotent). Databases that instead declare a standalone `UNIQUE(kind, date)`
index simply have that index dropped. The `kv` table gets the same treatment: it used to be unique on
`key` alone, so a second user writing the same key (e.g. the habit cache `habits.profile`) hit
`UNIQUE constraint failed: kv.key`; it is rebuilt as unique on `(user_id, key)`, old rows preserved and
the migration is idempotent.

**Portable**: SQLite snapshot backups (`data/backups/`, admin-only, i.e. the first registered user) plus
JSON export/import. Export/import fidelity: `created_at`, `updated_at`, `summary`, `meta` and `context`
are preserved, projects are re-linked by name, and subtask `parent_id`s are restored through an id map
from the exported file; duplicate items are skipped by raw text (`raw`).

On every surface the Data page writes exports/downloads straight to the local download folder: Android
through the native `CordisFiles` bridge (MediaStore, no storage permission), the desktop client through
`window.pywebview.api.save_file`, and a plain browser through `<a download>`.

---

## API

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Health / record counts / AI status |
| GET | `/api/config` | Effective configuration (secrets redacted) |
| GET/PUT | `/api/settings` | Read/write global settings (AI base URL/model/key…, admin only = first registered user) |
| POST | `/api/auth/register` | Register `{"username","password"}` |
| POST | `/api/auth/login` | Log in, returns a bearer token |
| GET | `/api/auth/me` | Current user |
| POST | `/api/auth/logout` | Log out |
| POST | `/api/capture` | Quick capture `{"text","source","meta"}` → stored immediately; `refining:true` means intent refinement is still running |
| POST | `/api/capture/upload` | File/voice capture `{"filename","content_base64","source"}` → parsed by the backend; voice returns `asr` / `asr_provider` / `asr_error` (plus `refining`) |
| GET | `/api/items` | List (filter by type/project/tag/status/date) |
| GET/PATCH | `/api/items/{id}` | Read / update (title, type, due, priority, tags…); poll `parse_provider` to see refinement outcome |
| DELETE | `/api/items/{id}` | Delete (cascades subtasks and reminders, irreversible) |
| POST | `/api/items/{id}/complete` | Mark complete |
| POST | `/api/items/{id}/reparse` | Re-parse |
| POST | `/api/items/{id}/split` | Split into subtasks `{"tasks":[...]}` (or a comma-separated string) |
| GET | `/api/items/{id}/children` | Subtask list |
| GET | `/api/search?q=&type=&tag=&…` | Hybrid search |
| GET | `/api/ask?q=` | Natural-language Q&A (with sources) |
| GET | `/api/today?date=` | Today view (overdue/today/next 7 days/waiting/new; `counts.within7` includes today) |
| GET | `/api/projects` | Project list |
| GET | `/api/reminders` | Reminder list |
| POST | `/api/reminders` | Create a reminder `{"item_id","due_at","kind"}` |
| PATCH | `/api/reminders/{id}` | `{"action":"snooze|cancel|fire","minutes"}` |
| GET | `/api/notifications` | Notification outbox |
| POST | `/api/notifications/{id}/read` | Mark as read |
| GET/POST | `/api/reviews/daily` | Daily review (POST generates; regenerating the same date updates) |
| GET/POST | `/api/reviews/weekly` | Weekly review (same) |
| GET | `/api/entities` | Entity list |
| GET | `/api/graph` | Entity graph |
| GET | `/api/suggestions` | Proactive suggestions |
| POST | `/api/suggestions/action` | Apply a suggestion `{"action":"complete|reschedule_today|reschedule_next_day|delete|merge","item_ids":[…]}` |
| GET | `/api/backup` | Backup list (admin) |
| POST | `/api/backup` | Create a snapshot `{"label"}` (admin) |
| GET | `/api/backup/{name}/download` | Download a backup (sqlite file) |
| POST | `/api/backup/{name}/restore` | Restore that backup (takes a fresh safety backup first) |
| GET | `/api/export.json` | Export JSON (migration / external tools) |
| POST | `/api/import.json` | Import JSON (deduplicated by raw text, keeps timestamps/projects/parent links) |
| GET | `/api/habits` | Habit profile (usual hours/projects/tags/types + learned "tag → project" rules); returns `profile` / `asr` / `modes`; `?refresh=1` forces a recompute |
| POST | `/api/habits/rebuild` | Recompute the habit profile (idempotent) |

> All `/api/*` routes accept either `Authorization: Bearer <token>` or `?token=` (the latter is handy for download links).

---

## Rule-based parsing (offline heuristic)

Extracts structured JSON from a single natural-language sentence:
```
type / title / summary / tags / people / projects / project /
due_at / priority / context / actions / confidence
```
- Types: task / event / idea / reference / note (keyword scoring plus URL/time corrections)
- Time: Chinese relative expressions (today/tomorrow/the day after/next week/X month X day/3pm…, with sensible AM/PM defaults)
- Priority: urgent/important/when-free → P0…P3
- **Works without any LLM**; with an LLM attached, rules answer instantly and the LLM improves accuracy — the two layers do not conflict

---

## Configuration (config.toml)

- `server.host/port/data_dir/web_dir`; optional HTTPS: `https_port` + `tls_cert` / `tls_key` (skipped automatically when the certificate is missing or `_ssl` is unavailable)
- `time.timezone`, `reminders.poll_interval`
- `db.path` (empty = `data/brain.db`)
- `search.use_fts5` = `auto|on|off`, `search.semantic` = `auto|off`
- `capture.provider` = `auto` (default: rules store first, background LLM refines intent) / `heuristic` (rules only)
- `asr.provider` = `auto` (default: remote OpenAI-compatible → local whisper → local vosk) / `openai` / `whisper` / `vosk` / `off`
  - `asr.base_url` (e.g. `https://api.openai.com/v1`, a self-hosted faster-whisper gateway; empty = no remote),
    `asr.api_key` (empty = reuse the `[ai.openai]` key when it is the same gateway), `asr.model`
    (default `whisper-1`), `asr.language`, `asr.vosk_model`, `asr.timeout` (default 60s)
- `habits.auto_project` = `auto` (default: tag rules → dominant-project fallback) / `tags` (tag rules only) / `off`
- `habits.suggest_boost` = `auto` (default: habit-weighted suggestions) / `off`
- `ai.provider` = `openai` (default, wired to the Kilo free gateway) / `ollama` / `heuristic`
  — **Q&A, capture and refinement all share this configuration**
- `ai.openai.{base_url, api_key, model, embed_model}`: defaults to
  `https://api.kilo.ai/api/gateway` and `kilo-auto/free` (**free models need no API key**);
  for other gateways set your own `api_key` (the `OPENAI_API_KEY` environment variable is also
  supported, but only for the official endpoint) and optionally set `embed_model` (the Kilo gateway
  has no embedding model, so leaving it empty means keyword-only retrieval)
- `ai.ollama.{base_url, model, embed_model}`: local Ollama, e.g. `http://127.0.0.1:11434`

> With `ai.provider = "openai"` pointing at the Kilo free gateway, everything works out of the box.
> When the network is unavailable or the API fails, parsing falls back to rules and keyword retrieval
> without blocking capture. For fully offline use, switch back to `heuristic` in Settings.
> The free gateway's latency varies a lot (10–45s in practice), which is why capture is asynchronous:
> results appear first and the AI refreshes them when it answers.

---

## Directory layout

```
cordis/            plugin kernel (pre-existing, unchanged)
brain/
  config.py        configuration plugin
  db.py            SQLite + FTS5 storage (with _migrate forward-compatible migrations)
  ai.py            AI gateway + heuristic parsing + offline embeddings
  textproc.py      Chinese 2-gram tokenizer (jieba optional)
  timeutil.py      Chinese relative-time parsing
  pipeline.py      capture / parsing / search / today / Q&A (including background LLM refinement)
  remind.py        reminder scheduling
  notify.py        notification outbox
  review.py        daily / weekly reviews
  entities.py      entity graph
  suggestions.py   proactive suggestions
  workflow.py      workflows (split subtasks / link projects)
  habits.py        habit profile (usual hours/projects/tags → default times, project auto-linking, suggestion weighting)
  sync.py          backup / restore / JSON migration (high-fidelity import & export)
  io.py            file & voice parsing (PDF/DOCX/HTML + ASR: compatible endpoint / whisper / vosk)
  http.py          REST API + static file serving
  util.py          utilities
web/
  index.html       SPA skeleton (today/capture/ask/search/reminders/reviews/graph/suggestions/data)
  app.css          styles: dark/light themes, responsive layout, button hover states
  app.js           frontend logic: API calls, rendering, recording (native bridge + getUserMedia fallback), file saving
android/           APK build (buildozer + p4a, see android/README.md)
desktop/           Windows desktop shell (pywebview, optional)
server.py          entry point (asyncio startup + --selftest)
config.toml        configuration
tests/             smoke + deployment tests
data/              runtime data (SQLite, backups, uploads, TLS certificates; gitignored)
```

> The root `.gitignore` excludes runtime data and build artifacts: `data/` (SQLite/WAL, backups,
> uploads, TLS certificates), `__pycache__/`, `*.log`, `android/dist/`, `*.apk`, `desktop/dist/`, etc.
> Source files, `config.toml` and `android/screenshots/` are still tracked.

---

## Roadmap

Done: P0 capture/search/Q&A → P1 reminders/reviews/graph → **P2 proactive suggestions, backup &
migration, workflows, file/voice capture** → **P2.5 mobile-first frontend (theming, bottom tabbar,
hover states), Android APK, Windows desktop client** → **P2.6 async LLM intent parsing for capture,
per-platform download handling for exports/backups, high-fidelity import & export, automatic repair of
legacy review constraints** → **P3.1 speech-to-text (OpenAI-compatible ASR with whisper/vosk
fallback), reminder sound + system notifications, habit profile** → **P3.2 deeper habits: auto-linking
your usual project (tag → project learning) and habit-weighted suggestion ordering**.

Next (P3 candidates):
1. **Real incremental multi-device sync**: today it is local-first snapshot backup plus JSON migration
   (enough for device moves and external tools); live cross-device sync needs sync infrastructure
   (versioning, conflict policy, an incremental log) and deserves its own design round.
2. **Desktop tray**: sound + system notifications are in place; a always-resident tray icon needs extra
   dependencies (pystray etc.), which would break the zero-dependency promise — an optional
   enhancement for packaged builds.
3. **Habits, further**: predict "what to work on today" from weekday/time patterns, and surface the usual
   project as a capture-time suggestion.
4. **Long-audio ASR**: transcription is synchronous and bounded by `asr.timeout` (60s default); long
   recordings want an async job with progress reporting.

Risk mitigation: LLM parsing errors → confidence scores + editable + re-parseable; idempotent reminder
deduplication; habits only affect ordering/defaults and never rewrite content; privacy → local-first,
always exportable and backup-able.
