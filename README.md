# OriSelf Next · App

[English](./README_EN.md)

**可自部署的 OriSelf 完整实例。** 由 Next.js 前端 + FastAPI 后端组成，加载 [`niuniu-869/oriself-next`](https://github.com/niuniu-869/oriself-next) skill 作为产品本体。

官方部署：[next.oriself.com](https://next.oriself.com)

---

## 这个仓库是什么

OriSelf 的架构是分层的：

| 层 | 做什么 | 在哪 |
|---|---|---|
| **Skill** | 访谈方法论 · 一组 markdown | [`niuniu-869/oriself-next`](https://github.com/niuniu-869/oriself-next) |
| **Server** | FastAPI · 对话循环 + guardrails + LLM 适配 | 本仓库 `server/` |
| **Web** | Next.js · 落地页 / 对话页 / 报告页 | 本仓库 `web/` |

**产品本体是 skill。** 本仓库是把 skill 包装成完整服务的参考实现。Skill 作为 git submodule 引入，升级通过 bump submodule 完成——我们不在这里改 skill 的 markdown。

---

## 自部署（5 分钟）

```bash
git clone --recurse-submodules https://github.com/niuniu-869/oriself-next-app.git
cd oriself-next-app
cp .env.example .env
# 编辑 .env，填入任一 LLM API key（DeepSeek / Qwen / Kimi / OpenAI）
docker compose up --build
```

打开 http://localhost:3000 就能用了。

不想装 Docker？也可以分别跑前后端（见下面「开发模式」）。

---

## 架构

```
 ┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
 │   Next.js       │ HTTP │   FastAPI       │      │   LLM Provider  │
 │   (Vercel)      ├─────→│   (Fly.io)      ├─────→│   DeepSeek/...  │
 │                 │      │                 │      │                 │
 │   Landing       │      │   SkillRunner   │      └─────────────────┘
 │   Letter page   │      │     ↓           │
 │   Issue page    │←─────│   Guardrails    │
 │   (iframe)      │      │     ↓           │
 └─────────────────┘      │   SQLite /      │
                          │   Postgres      │
                          └────────┬────────┘
                                   │
                          ┌────────────────────┐
                          │  skill-repo/       │  ← git submodule →
                          │    skills/oriself/ │    niuniu-869/oriself-next
                          │    SKILL.md ...    │
                          └────────────────────┘
```

关键设计：

- **报告页是 iframe sandbox**。LLM 生成的 HTML 完全沙箱化，不能访问父页面。
- **每个 MBTI 类型独立视觉**。Skill 在收敛时指示 LLM 生成完全不同的设计——我们这边只提供信封和装订线。
- **Skill 是产品本体**。改访谈方法论 = 去 skill 仓库改 markdown → bump submodule。改前后端 = 改本仓库代码。两者干净分离。

---

## 开发模式

### 前端

```bash
cd web
pnpm install
pnpm dev                    # :3000
```

环境变量（`web/.env.local`）：
```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### 后端

```bash
cd server
pip install -e ".[dev]"

# 确保 skills/ submodule 已初始化
cd .. && git submodule update --init --recursive

# 跑起来（mock provider，不需要 API key）
ORISELF_PROVIDER=mock uvicorn oriself_server.main:app --reload  # :8000

# 跑测试
cd server && pytest
```

Swagger UI: http://localhost:8000/docs

### Skill 升级

每周 GitHub Action 自动开 PR 同步 skill 最新版。手动升级：

```bash
cd skill-repo
git pull origin main
cd ..
git add skill-repo
git commit -m "bump skill to v2.x.x"
```

---

## 目录结构

```
oriself-next-app/
├── skill-repo/                    # git submodule → niuniu-869/oriself-next
│   └── skills/oriself/            # ↑ 真正的 skill 在这
│
├── web/                           # Next.js 15 · next.oriself.com
│   ├── app/
│   │   ├── page.tsx               #   / · 落地页
│   │   ├── letters/[id]/page.tsx  #   /letters/:id · 对话页
│   │   └── issues/[id]/page.tsx   #   /issues/:slug · 报告页（iframe）
│   ├── components/
│   ├── lib/
│   └── styles/
│
├── server/                        # FastAPI · api.oriself.com
│   ├── oriself_server/
│   │   ├── main.py
│   │   ├── routes/
│   │   │   ├── letters.py         #   /letters/*
│   │   │   └── issues.py          #   /issues/* · 公开报告
│   │   ├── skill_runner.py
│   │   ├── guardrails.py
│   │   └── llm_client.py
│   └── tests/
│
├── deploy/
│   ├── vercel.json
│   └── fly.toml
│
├── docker-compose.yml
├── .env.example
└── .github/workflows/
    ├── ci.yml
    └── bump-skill.yml            # 每周自动 PR skill 升级
```

---

## 开源

**Apache 2.0**。随你 fork、改造、自托管。

Skill 本体在 [`niuniu-869/oriself-next`](https://github.com/niuniu-869/oriself-next) 有单独的许可证（也是 Apache 2.0）。
