<!-- BEGIN ZCF:AUTO-GENERATED (server) -->
[根目录](../CLAUDE.md) > **server**

# server · FastAPI 后端

> 生成时间：2026-04-18 22:30:17 · 自动区间外的手写内容在重新运行时保留。

## 一、模块职责

- 承载 OriSelf 的核心对话循环：把用户每一轮消息拼进 skill prompt、调 LLM 流式出 token、解析末行 STATUS sentinel、落库、驱动 phase 前进。
- 报告生成（converge）：在满足最低轮数（`MIN_CONVERGE_ROUND=6`）后独立调用 LLM，校验 `ConvergeOutput`，清洗 HTML，分配 issue slug，落入 `test_results`。
- 向前端暴露：`/letters/*`（对话 + 报告）、`/issues/*`（报告元数据 / 渲染 / 公开开关）、`/feedback`、`/health`。
- 多 provider 适配（OpenAI compatible 家族 + mock），提供 CLI 便于本地跑一封信。
- 把 skill-as-markdown（`skill-repo/skills/oriself/*.md`）抽象成 `SkillBundle`，给两种 runner 复用。

### v2.4 设计哲学（见 `schemas.py` / `guardrails.py` 顶部 docstring）

- **对话轮不走 JSON schema、不做 retry、不 fallback**：LLM 出什么用户看什么，不满意用户点「重写这轮」。
- **只有 4 条硬拦截**：轮数上限、MBTI 正则、`report_html` XSS、4 字母 MBTI 一致性。其余品味约束全写进 skill prompt（`SKILL.md` 等）。
- **唯一结构化点**：报告生成 `ConvergeOutput`，允许最多 3 次 retry。

## 二、入口与启动

| 入口 | 作用 |
|---|---|
| `oriself_server/main.py::create_app()` | FastAPI app；加载 dotenv、挂 CORS、挂 3 个 router、`/health`、startup `init_db()` |
| `oriself_server/cli.py` | `python -m oriself_server.cli --provider mock` 终端模式 |

启动：

```bash
cd server
pip install -e ".[dev]"                                # [postgres] 可选
ORISELF_PROVIDER=mock \
  uvicorn oriself_server.main:app --reload             # :8000

# 或：
python -m oriself_server.cli --provider mock           # 内置 :quit / :rewrite / :state

pytest --cov=oriself_server --cov-report=term          # CI 同命令
```

Docker：`server/Dockerfile` → Python 3.12-slim + `pip install -e .` + uvicorn；`VOLUME /data`（SQLite）；挂 `skill-repo` 到 `/app/skill-repo`；`HEALTHCHECK curl /health`。

## 三、对外接口（HTTP）

### 3.1 `/letters`（`routes/letters.py`）

| Method | Path | Body / Query | 返回 | 说明 |
|---|---|---|---|---|
| POST | `/letters` | `{provider?, domain?}` → `CreateLetterRequest` | `CreateLetterResponse` | 新建会话；`provider` 缺省读 `ORISELF_PROVIDER`，再兜 `"mock"` |
| POST | `/letters/{id}/turn` | `{user_message}` (1..4000) | **SSE 流** | `event: token/done/error`；`done` 携带 `{round,status,visible}` |
| POST | `/letters/{id}/turn/rewrite` | `{hint?: ≤500}` | SSE | 把最近非 discarded 轮标 `discarded=true`，再用同样 `user_message` + hint 重跑 |
| POST | `/letters/{id}/result` | — | `ResultResponse` | 已生成则直接读 `test_results`；没生成则跑 `ReportRunner.compose`（3 次 retry），生成 slug、清洗 HTML、落库；轮数 `< MIN_CONVERGE_ROUND` 返 400 |
| GET | `/letters/{id}/state` | — | `StateResponse` | `round_count / status / last_status / has_report / issue_slug` |
| GET | `/letters/{id}/transcript` | — | `TranscriptResponse` | 只返非 discarded 轮，展开为 `you / oriself` 交替 |

SSE 事件格式（见 `_sse()`）：

```
event: token
data: {"delta": "..."}

event: done
data: {"round": N, "status": "CONTINUE|CONVERGE|NEED_USER", "visible": "..."}

event: error
data: {"message": "..."}
```

**早收束降级**：LLM 在 `round < MIN_CONVERGE_ROUND` 时声明 CONVERGE 会被服务端静默改写成 CONTINUE（`routes/letters.py::_stream_turn_core`），防止用户 R2 就跳到报告页再也写不了字。

**幂等**：同一 `(session_id, round_number)` 只允许最多一条 `discarded=False`；重复 POST 同一轮直接 409。DB 层过去的 `uq_session_round_discarded` 唯一索引已在 `database.py::init_db` 里 `DROP INDEX IF EXISTS`。

### 3.2 `/issues`（`routes/issues.py`）

| Method | Path | 返回 | 说明 |
|---|---|---|---|
| GET | `/issues/{slug}` | `IssueResponse` | 元数据；`is_public=False` 时 403 |
| GET | `/issues/{slug}/render` | HTML | 给前端 iframe 用；强制 `Content-Security-Policy: sandbox allow-scripts allow-same-origin; default-src 'self' 'unsafe-inline' https://fonts.*; ...` |
| PATCH | `/issues/{slug}/publish` | `IssueResponse` | 切换 `is_public`；**MVP 不鉴权**，生产需换 owner token |

### 3.3 `/feedback`（`routes/feedback.py`）

| Method | Path | 返回 | 说明 |
|---|---|---|---|
| POST | `/feedback` | `{id, created_at}` (201) | 匿名；`letter_id` / `issue_slug` 必须存在；per-IP token bucket 限频：10 分钟 5 条 |

### 3.4 `/health`

`GET /health` → `{"status":"ok","version":__version__}`（`oriself_server/__init__.py::__version__="2.0.0"`；注：与 `skill_version` 默认 `"2.4.0"` 并存）。

## 四、关键依赖与配置

`pyproject.toml`：

- 运行：`fastapi>=0.110 · uvicorn[standard]>=0.27 · pydantic>=2.5 · sqlalchemy>=2.0 · httpx>=0.26 · pyyaml>=6.0 · python-dotenv>=1.0`
- dev：`pytest>=8.0 · pytest-asyncio>=0.23 · pytest-cov>=4.1`
- `[tool.pytest.ini_options] asyncio_mode = "auto" testpaths = ["tests"]`

环境变量：

| 变量 | 作用 | 默认 |
|---|---|---|
| `ORISELF_PROVIDER` | 默认 provider | —（请求里不带且未设时由 `make_backend` 兜 `"mock"`） |
| `ORISELF_DB_PATH` | SQLite 路径 | `oriself_v2.db` |
| `ORISELF_{PROVIDER}_API_KEY` | 各 provider 密钥 | — |
| `ORISELF_CORS_ORIGINS` | 逗号分隔允许 origin | `*`（当为空） |
| `ORISELF_SKILL_ROOT` | skill 根目录 | `../skill-repo/skills/oriself`（相对 `__file__`） |

`main.py` 启动时会尝试从 `<repo>/.env` 和 `<repo>/server/.env` 两个候选加载 dotenv（找不到 `python-dotenv` 也不报错）。

## 五、数据模型（`models.py`）

ORM 使用 SQLAlchemy 2.x `DeclarativeBase`。三张主表：

| 表 | 关键字段 | 约束 |
|---|---|---|
| `test_sessions` | `session_id (UUID, PK)`, `provider`, `domain`, `skill_version="2.4.0"`, `status∈{active,completed,failed}`, `prefs_json` | 1:N `conversations`，1:1 `test_results` |
| `conversations` | `id`, `session_id (FK)`, `round_number`, `user_message`, `oriself_text`, `raw_stream`, `status_sentinel∈{CONTINUE,CONVERGE,NEED_USER}`, `discarded` | Index `ix_conv_session_round_desc`、`ix_conv_session_status`；**不再有 UniqueConstraint**（v2.4.x 废除） |
| `test_results` | `session_id (unique FK)`, `mbti_type`, `insight_json`, `card_json`, `confidence_json`, `issue_slug (unique, index)`, `issue_title`, `issue_html`, `issue_is_public`, `issue_generated_at` | 1 session 1 report |
| `feedbacks` | `id`, `letter_id (FK nullable)`, `issue_slug`, `rating(1..5)`, `text`, `contact`, `user_agent` | Index `ix_feedback_letter` |

**v2.4 vs v2.3 的数据层裁剪**（见 `models.py` 顶注释）：`Conversation` 表删掉了 `action_json / action_type / dimension_targeted / turn_state / retry_count`，加了 `oriself_text / raw_stream / status_sentinel / discarded`；`EvidenceRecord` 整张表废弃。

### Pydantic schemas（`schemas.py`）

- 常量：`MAX_ROUNDS=30`、`DEFAULT_TARGET_ROUNDS=20`、`MIN_CONVERGE_ROUND=6`、`REPORT_MAX_RETRIES=3`、`ONBOARDING_ROUND=1`。
- `UserPreferences`：`style`、`target_rounds(6..30)`、`pace`、`opening_mood(≤200)`、`note(≤300)`。
- `ConvergeOutput`：`mbti_type (pattern EI/SN/TF/JP)` + `confidence_per_dim` + 3 段 `InsightParagraph` + `CardData` + `report_html`（1000..80000 chars，必须 `<!doctype html>` + `<html>...</html>`）。向后兼容旧两种 `confidence_per_dim` 形态，最终**以 `confidence_per_dim` 为单一真相源**派生 `mbti_type` 并覆盖 card。

## 六、代码地形

```
server/
├── pyproject.toml              # 包定义 + pytest config
├── README.md                   # 最小开发说明
├── Dockerfile                  # 多阶段？否——单阶段 python:3.12-slim
├── oriself_server/
│   ├── __init__.py             # __version__="2.0.0"
│   ├── main.py                 # create_app / dotenv / CORS / routers / /health
│   ├── cli.py                  # 终端互动跑一封信
│   ├── database.py             # SQLAlchemy engine + init_db + session_scope + reset_for_tests + 一次性 drop 遗留索引
│   ├── models.py               # ORM：TestSession / Conversation / TestResult / Feedback
│   ├── schemas.py              # Pydantic + MAX/MIN 常量 + ConvergeOutput 派生逻辑
│   ├── skill_loader.py         # SkillBundle + compose_conversation_prompt / compose_converge_prompt + lru_cache
│   ├── skill_runner.py         # TurnRunner.stream_turn / ReportRunner.compose / advance_state / choose_phase_key
│   ├── guardrails.py           # parse_status_sentinel / verify_report_html_shape / verify_report_html_consistency
│   ├── llm_client.py           # LLMBackend ABC + openai_compatible + MockBackend + make_backend
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── letters.py          # 对话 + 报告主 router
│   │   ├── issues.py           # 报告元数据 / 渲染（CSP sandbox）/ 公开开关
│   │   └── feedback.py         # 匿名反馈 + per-IP rate limit
│   └── utils/
│       ├── __init__.py
│       ├── prompt_sanitize.py  # sanitize_user_input（用户消息进 prompt 前）
│       └── html_sanitize.py    # sanitize_report_html / escape_user_quote
└── tests/
    ├── __init__.py
    ├── fixtures/__init__.py
    ├── test_v24_smoke.py       # STATUS / guardrails / Mock backend 路径
    └── test_skill_loader.py    # SkillBundle 载入 + prompt 组装
```

## 七、关键交互时序

**正常对话一轮（`POST /letters/{id}/turn`）**：

```
client ─► FastAPI
          _load_session_state (DB) 
          TurnRunner.stream_turn
            · compose_conversation_prompt(SKILL + ETHOS + domain + phase + techniques + exemplary)
            · history = (user, oriself_visible) 配对
            · backend.stream_text → 逐 chunk yield
          ├─ yield token frames → SSE
          └─ 结束后 parse_status_sentinel → 剥 STATUS / visible
          护栏：round < MIN_CONVERGE_ROUND 且 status==CONVERGE → 降级 CONTINUE
          _persist_turn：conversations 表插入，R2 还会解析 preferences 写 prefs_json
          yield done frame (round, status, visible)
```

**报告生成（`POST /letters/{id}/result`）**：

```
_load_session_state
round_count < 6 → 400
ReportRunner.compose (3 次 retry 在 skill_runner 内部)
  · compose_converge_prompt
  · backend.complete_json → dict
  · ConvergeOutput 校验 + guardrails.verify_report_html_shape/consistency
  · 失败累计 error_reasons
生成唯一 slug（冲突最多 3 次重试）
sanitize_report_html + escape pull_quotes
写 test_results，sess.status="completed"
```

## 八、测试与质量

- `server/tests/test_v24_smoke.py`：
  - STATUS sentinel 解析（含 CONVERGE / 无 sentinel 兜底）
  - guardrails：`verify_report_html_shape` / `verify_report_html_consistency`
  - Mock backend happy path
- `server/tests/test_skill_loader.py`：
  - SkillBundle 载入（SKILL/ETHOS/CONVERGE + mbti domain + 各技法 + exemplary；**显式断言 `banned-outputs` 不再存在**）
  - conversation prompt 组装
- CI（`.github/workflows/ci.yml::server`）：Python 3.12 + `pip install -e .[dev]` + `pytest --cov=oriself_server`。
- **缺口**：
  - 缺 endpoint-level 集成测（FastAPI `TestClient` 对 `/letters/*` 的 SSE 流）；
  - 缺 provider 真实调用 smoke（`openai_compatible` 的鉴权 / 超时分支）；
  - 无 mypy 或 ruff 配置（`pyproject.toml` 未配）。

## 九、常见问题 (FAQ)

- **Q：为什么第二次点「重写这轮」就 500？**
  A：历史上 `conversations` 有 `UniqueConstraint(session_id, round_number, discarded)` 把 discarded 列也限成一条，导致重写第二次撞约束。修复见 commit `42250c6`，`database.py::init_db` 现会 `DROP INDEX IF EXISTS uq_session_round_discarded`。如果仍旧 409，多半是生产库还没重启经过 `init_db`。
- **Q：LLM 明明 R3 就写了 `STATUS: CONVERGE`，为什么用户还在对话？**
  A：服务端护栏：`round < MIN_CONVERGE_ROUND(6)` 的 CONVERGE 会被静默改为 CONTINUE（`routes/letters.py::_stream_turn_core`）。防止"R2 就跳报告页"。
- **Q：`issue_html` 是如何防 XSS 的？**
  A：两道闸：写入前 `utils/html_sanitize.sanitize_report_html` + `guardrails.verify_report_html_shape`；读出时 `/issues/:slug/render` 设 CSP `sandbox` + 前端 iframe `sandbox="allow-scripts"` 不给 `allow-same-origin`。
- **Q：Gemini 怎么配？**
  A：最近提交 `3fa0ac9` 新增了 `GEMINI_*` env 别名，走 302.ai 的 OpenAI compatible 端点；直接设 `ORISELF_PROVIDER=gemini`（或等价 provider 名）和对应 key 即可。

## 十、相关文件清单

- 包配置：`pyproject.toml`、`README.md`、`Dockerfile`
- 应用：`oriself_server/{main,cli,database,models,schemas,skill_loader,skill_runner,guardrails,llm_client}.py`
- 路由：`oriself_server/routes/{letters,issues,feedback}.py`
- 工具：`oriself_server/utils/{prompt_sanitize,html_sanitize}.py`
- 测试：`tests/{test_v24_smoke,test_skill_loader}.py`

## 十一、覆盖率与缺口

- 已读全文：`main.py`、`models.py`、`schemas.py`、`routes/letters.py`、`routes/issues.py`、`routes/feedback.py`、`database.py`、`pyproject.toml`、`Dockerfile`、`README.md`、`__init__.py`。
- 只读头部：`skill_runner.py`（前 80 行）、`llm_client.py`（前 60 行）、`skill_loader.py`（前 60 行）、`guardrails.py`（前 40 行）、`cli.py`（前 40 行）、`tests/test_v24_smoke.py`（前 40 行）、`tests/test_skill_loader.py`（前 30 行）。
- 完全未读：`utils/prompt_sanitize.py`、`utils/html_sanitize.py`。
- 优先深挖：
  1. `skill_runner.py` 第 80 行后：`choose_phase_key` / `advance_state` / `ReportRunner.compose` 3-retry 实现。
  2. `llm_client.py` 第 60 行后：openai compatible 的 SSE 解析、error 分支；MockBackend 如何造 CONVERGE 的合法 JSON。
  3. `guardrails.py` 第 40 行后：`verify_report_html_consistency` 的 MBTI 一致性扫描器。
  4. `utils/*.py`：XSS / prompt injection 的具体规则；测试覆盖。
  5. `tests/test_v24_smoke.py` 下半 + 补 `routes/letters.py` 的集成测。

## 十二、变更记录 (Changelog)

| 时间 | 内容 |
|---|---|
| 2026-04-18 22:30:17 | 初始化 `server/CLAUDE.md` |
| *(保留给手写记录)* | |

<!-- END ZCF:AUTO-GENERATED (server) -->
