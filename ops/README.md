# ops/ · 生产端运维脚本

生产环境用 **PM2**（没有 Docker）。这里放一套 "装一次 → 后面只用 `./ops/oriself.sh` 就够了" 的控制脚本。

## 首次部署

```bash
# 1) 装 pm2
npm i -g pm2

# 2) 装 server 依赖
cd server
pip install -e .
cd ..

# 3) 建 web 产物
cd web
pnpm install --frozen-lockfile
pnpm build
cd ..

# 4) 在仓库根放一份 .env（provider key / 端口等）
cat > .env <<EOF
ORISELF_PROVIDER=gemini
ORISELF_GEMINI_API_KEY=sk-xxx
ORISELF_GEMINI_BASE_URL=https://api.302.ai/v1
# 可选 · 端口默认 8000 / 3000
# ORISELF_SERVER_PORT=8000
# ORISELF_WEB_PORT=3000
EOF

# 5) 启
./ops/oriself.sh start

# 6) 开机自启（会打印一行 sudo，照抄执行）
./ops/oriself.sh install-startup
```

## 日常

| 命令 | 作用 |
|---|---|
| `./ops/oriself.sh status` | pm2 ls · 两个服务在不在 |
| `./ops/oriself.sh logs` | 滚动所有日志 |
| `./ops/oriself.sh logs oriself-server` | 只看服务端 |
| `./ops/oriself.sh restart` | 硬重启两个进程 |
| `./ops/oriself.sh reload` | 同上但带 `--update-env`（改过 `.env` 必用） |
| `./ops/oriself.sh stop` / `start` | 停 / 启 |
| `./ops/oriself.sh health` | `curl /health` + 首页 200 检查 |
| `./ops/oriself.sh deploy` | `git pull → pip install -e . → pnpm build → reload → health` 一条龙 |
| `./ops/oriself.sh kill` | pm2 delete（进程表也清掉） |

## 架构

两个 pm2 app：

```
oriself-server  · python3 -m uvicorn oriself_server.main:app  @ 127.0.0.1:8000
oriself-web     · node    node_modules/next/dist/bin/next start -p 3000  @ 127.0.0.1:3000
```

web 通过 `API_INTERNAL_URL=http://127.0.0.1:8000` 反代到 server；外部 nginx / caddy
把 443 打到 web 的 3000 即可，server 不对外。

## 文件

- `ecosystem.config.cjs` · pm2 的进程定义（路径相对 `__dirname`）
- `oriself.sh` · 运维总入口；子命令全部 `./ops/oriself.sh --help` 可查
- 日志默认写 `<repo>/logs/{server,web}.{out,err}.log`（已加入 `.gitignore`）

## 改了 `.env` 之后

PM2 会缓存第一次启动时的 env，改过 `.env` 必须 `reload`（不是 `restart` —— 两者在 fork 模式下行为一样，但只有 `reload` 走 `--update-env` 路径是我们加固过的）：

```bash
./ops/oriself.sh reload
```

## 看不惯 pm2 的原生命令怎么办

这些都是封装：

```bash
# 直接用 pm2 的
pm2 start  ops/ecosystem.config.cjs --update-env
pm2 logs   oriself-web --lines 100
pm2 describe oriself-server
pm2 monit   # 交互式 top
```
