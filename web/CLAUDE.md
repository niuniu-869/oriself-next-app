<!-- BEGIN ZCF:AUTO-GENERATED (web) -->
[根目录](../CLAUDE.md) > **web**

# web · Next.js 前端

> 生成时间：2026-04-18 22:30:17 · 自动区间外的手写内容在重新运行时保留。

## 一、模块职责

- 承载 OriSelf 的所有用户可见表面：首页（作品集风）、`/letters/new` 入口、`/letters/:id` 对话视图、`/issues/:slug` 报告壳。
- 作为 SSE 流的消费端：解析 `event: token / done / error` 三种事件，支持取消（`AbortSignal`）。
- 本地缓存「最近信件」列表（纯 `localStorage`，服务端不感知；`lib/history.ts`）。
- 通过 `next.config.mjs::rewrites` 把 `/api/*` 反代到后端，避免把 backend URL 暴露给浏览器；iframe 嵌入的 `/api/issues/:slug/render` 同样走此代理。
- 全局字体 / 设计 token 集中在 `app/layout.tsx` + `tailwind.config.ts`（Fraunces / Instrument Sans / JetBrains Mono / Noto Serif SC）。

## 二、入口与启动

| 入口 | 作用 |
|---|---|
| `app/layout.tsx` | 注入字体变量、`<CustomCursor />`、元数据 |
| `app/page.tsx` | Landing（`/`）：Hero + RecentLetters + colophon |
| `app/letters/new/page.tsx` | Server Component，`createLetter()` 后 `redirect('/letters/:id')` |
| `app/letters/[id]/page.tsx` | 拉 `getLetterState` + `getLetterTranscript`，交给 `letter-view.tsx` client 渲染 |
| `app/letters/[id]/letter-view.tsx` | 对话主界面（client） |
| `app/issues/[slug]/page.tsx` | Issue 壳；`getIssue` + `<IssueChrome>` + `<HistorySync>` |

启动：

```bash
cd web
pnpm install                # packageManager: pnpm@9.15.0
cp .env.local.example .env.local
pnpm dev                    # :3000
pnpm typecheck              # tsc --noEmit（CI 执行）
pnpm build && pnpm start    # 本地 prod；Docker 下 BUILD_STANDALONE=1
```

`next.config.mjs` 关键：

- `allowedDevOrigins: ['next.oriself.com']`
- `/api/:path*` → `${API_INTERNAL_URL || 'http://localhost:8000'}/:path*`
- 全站加 `X-Content-Type-Options=nosniff` / `Referrer-Policy=strict-origin-when-cross-origin` / `X-Frame-Options=DENY`；`/issues/:slug` 改为 `SAMEORIGIN` 以便分享时嵌入自己域。
- `output: 'standalone'` 仅在 `BUILD_STANDALONE=1` 时开启，避免本地 `pnpm build` 产生 warning。

## 三、对外接口（前端消费的后端契约）

`lib/api.ts` 是唯一的 HTTP 客户端，集中了 SSE 和 JSON 两套调用：

| 函数 | 后端端点 | 备注 |
|---|---|---|
| `createLetter(provider?, domain='mbti')` | `POST /letters` | 返回 `LetterCreateResponse` |
| `getLetterState(id)` | `GET /letters/:id/state` | 轮数 / 状态 / issue_slug |
| `getLetterTranscript(id)` | `GET /letters/:id/transcript` | 只返非 discarded 轮 |
| `sendTurnStream(id, msg, {onToken,onError,signal})` | `POST /letters/:id/turn` (SSE) | 逐字推 token；结束返回 `TurnDonePayload` |
| `rewriteLastTurn(id, {hint?})` | `POST /letters/:id/turn/rewrite` (SSE) | 标 discarded 后重新流 |
| `composeResult(id)` / `getResult` | `POST /letters/:id/result` | 触发 / 读取报告生成 |
| `getIssue(slug)` | `GET /issues/:slug` | 元数据 |
| `publishIssue(slug, isPublic)` | `PATCH /issues/:slug/publish` | 公开 / 私有切换 |
| `submitFeedback(payload)` | `POST /feedback` | 匿名反馈 |

SSE 解析见 `streamToDone`：按 `\n\n` 切 frame，识别 `event:` / `data:`，`done` 事件填充 `TurnDonePayload`（类型见 `lib/types.ts`）。

**关键 URL 约定**：

- 浏览器侧 `baseUrl()` 返回 `"/api"`（走 Next rewrite）。
- 服务端（Server Component）侧返回 `process.env.API_INTERNAL_URL || 'http://localhost:8000'`。

## 四、关键依赖与配置

| 配置 | 作用 |
|---|---|
| `package.json` | `next@^15.1`、`react@^19`、`tailwindcss@^3.4.17`、`eslint-config-next@^15.1`；pnpm workspace 未启用，仅单包 |
| `tsconfig.json` | `strict` + `moduleResolution: bundler` + `paths: @/* → ./*` |
| `tailwind.config.ts` | **关闭默认调色盘**，只暴露 `paper/ink/accent/rule` 四组 + 字体三族 + 两条动画（`settle` / `rise` / `blink`） |
| `postcss.config.mjs` | tailwind + autoprefixer 标准链 |
| `app/globals.css` *(未读)* | CSS 变量真实落地处；token 与 tailwind 同步 |
| `.env.local.example` | `NEXT_PUBLIC_API_URL` + `API_INTERNAL_URL` |
| `Dockerfile` | 三段 `deps → builder → runner`，runner 用 `nextjs` 非 root；`CMD ["node","server.js"]` |
| `.dockerignore` | 未读细节 |

运行时环境变量：

- `NEXT_PUBLIC_API_URL`：只在 build 时注入到 client bundle（当前代码主要用 `/api` rewrite，这个变量主要留作备用）。
- `API_INTERNAL_URL`：Server Component fetch 与 `next.config.mjs rewrites` 使用，默认 `http://localhost:8000`。
- `BUILD_STANDALONE=1`：切换 Next 的 `output: 'standalone'`，Dockerfile 已显式设置。

## 五、代码地形

### 5.1 目录

```
web/
├── app/
│   ├── layout.tsx             # 字体 + CustomCursor + metadata
│   ├── page.tsx               # Landing
│   ├── letters/
│   │   ├── new/page.tsx       # Server Component → redirect
│   │   └── [id]/
│   │       ├── page.tsx       # 拉 state/transcript
│   │       └── letter-view.tsx# 对话主 client 组件
│   └── issues/[slug]/page.tsx # Issue 壳（iframe sandbox）
├── components/
│   ├── masthead.tsx           # 通用顶栏
│   ├── primitives/
│   │   └── custom-cursor.tsx  # 自绘光标
│   ├── home/
│   │   └── recent-letters.tsx # 读取 localStorage 的最近信件列表
│   ├── letter/
│   │   ├── composer.tsx       # 输入框（lift z-index 修复见 commit 5ec07b8）
│   │   └── turn.tsx           # 单轮渲染
│   ├── issue/
│   │   └── issue-chrome.tsx   # Issue 页底部 chrome 条
│   ├── history/
│   │   └── history-sync.tsx   # 将当前 issue 写回 localStorage
│   └── feedback/
│       └── feedback-sheet.tsx # 反馈抽屉
└── lib/
    ├── api.ts                 # 唯一 HTTP/SSE 客户端
    ├── types.ts               # 与 server schemas 对齐的 TS 类型
    └── history.ts             # localStorage（key: "oriself:letters:v1"，上限 10 条）
```

### 5.2 设计 token 要点

- `app/page.tsx` 的 Hero 用 Fraunces variable axes（`opsz 144, SOFT 100, WONK 1`）+ italic + `letterSpacing: -0.045em`，这是全站唯一"招牌级"字号调教。
- 自定义 `.fraunces-body-soft` 类名出现在多处页面；真实实现应在 `app/globals.css`（本次未读）。
- 颜色只能用 tailwind 的 `paper-*/ink-*/accent-*/rule-*`，不要再引入默认灰阶。

## 六、数据模型

前端不持有持久化数据。状态分两层：

1. **服务端持久化 proxy**：`LetterState` / `LetterTranscript` / `LetterResult` / `IssueMeta`（`lib/types.ts`），形状跟后端 `schemas.py` + `routes/letters.py::TranscriptResponse/StateResponse/ResultResponse` 一一对应。
2. **浏览器本地存储**：`LocalLetterEntry`（`lib/history.ts`）。key=`oriself:letters:v1`，上限 `MAX_ENTRIES=10`，字段含 `letterId / updatedAt / roundCount / status / issueSlug / mbtiType / cardTitle`。

## 七、测试与质量

- **单测**：无（项目目前未引入 vitest/jest）。
- **静态分析**：`pnpm typecheck`（tsc）+ `pnpm lint`（next lint / eslint-config-next）。
- **CI**：`.github/workflows/ci.yml::web` job 只跑 install → typecheck → build（带 `NEXT_PUBLIC_API_URL=http://localhost:8000`）。
- **建议补**：对 `lib/api.ts::streamToDone` 写单测（Web Streams Mock），对 `lib/history.ts` 写 JSDOM 单测，对 `/issues/:slug` iframe 渲染做 Playwright e2e（覆盖 SAMEORIGIN 头）。

## 八、常见问题 (FAQ)

- **Q：为什么浏览器里 fetch `/api/...` 能打到后端？**
  A：`next.config.mjs::rewrites` 将 `/api/*` 代理到 `API_INTERNAL_URL`。这样客户端代码 / 网络面板都看不到真实后端地址，iframe 嵌入 issue 也走同一个代理。
- **Q：报告页看不到内容？**
  A：iframe 的 `src` 是 `/api/issues/:slug/render`，后端返回 CSP `sandbox` + 内联 HTML；iframe 标签上 `sandbox="allow-scripts"` 但**没有 `allow-same-origin`**，因此报告无法访问父窗口——这是设计。
- **Q：首页"最近信件"被清空了？**
  A：纯 `localStorage`，换设备 / 清浏览器数据就没了；这是有意的：没有账号体系。

## 九、相关文件清单

- `package.json` / `pnpm-lock.yaml` / `tsconfig.json` / `next.config.mjs` / `tailwind.config.ts` / `postcss.config.mjs` / `Dockerfile` / `.env.local.example`
- `app/layout.tsx` / `app/page.tsx` / `app/letters/new/page.tsx` / `app/letters/[id]/page.tsx` / `app/letters/[id]/letter-view.tsx` / `app/issues/[slug]/page.tsx`
- `components/letter/composer.tsx` / `components/letter/turn.tsx` / `components/home/recent-letters.tsx` / `components/issue/issue-chrome.tsx` / `components/history/history-sync.tsx` / `components/feedback/feedback-sheet.tsx` / `components/masthead.tsx` / `components/primitives/custom-cursor.tsx`
- `lib/api.ts` / `lib/types.ts` / `lib/history.ts`

## 十、覆盖率与缺口

- 已读：`package.json`、`tsconfig.json`、`next.config.mjs`、`tailwind.config.ts`、`.env.local.example`、`Dockerfile`、`app/layout.tsx`、`app/page.tsx`、`app/letters/new/page.tsx`、`app/letters/[id]/page.tsx`（头部）、`app/issues/[slug]/page.tsx`（头部）、`lib/api.ts`、`lib/types.ts`、`lib/history.ts`（头部）。
- 未读 / 只窥一眼：`app/letters/[id]/letter-view.tsx`、`app/globals.css`、所有 `components/**` 实现、`lib/history.ts` 写入路径、`next-env.d.ts`、`postcss.config.mjs`。
- 优先深挖：`components/letter/composer.tsx`（commit `5ec07b8` z-index 修复上下文）、`components/letter/turn.tsx`（SSE 渲染形态）、`app/globals.css`（`.fraunces-body-soft` 真身）。

## 十一、变更记录 (Changelog)

| 时间 | 内容 |
|---|---|
| 2026-04-18 22:30:17 | 初始化 `web/CLAUDE.md` |
| *(保留给手写记录)* | |

<!-- END ZCF:AUTO-GENERATED (web) -->
