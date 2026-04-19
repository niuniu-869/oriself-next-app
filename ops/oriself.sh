#!/usr/bin/env bash
# OriSelf · 生产端 PM2 控制脚本
# 依赖：pm2（全局安装），pnpm（web 用），python3 + 在 server/ 下执行过 `pip install -e .`
#
# 用法（在仓库根执行均可）：
#   ./ops/oriself.sh start       启动 server + web
#   ./ops/oriself.sh stop        停止两个服务
#   ./ops/oriself.sh restart     进程级重启
#   ./ops/oriself.sh reload      等价 restart（fork 模式无零停机语义，仍提供别名）
#   ./ops/oriself.sh status      pm2 ls
#   ./ops/oriself.sh logs [name] 看日志；name 可为 oriself-server / oriself-web
#   ./ops/oriself.sh tail [name] 等价 logs --lines 50 + --raw
#   ./ops/oriself.sh kill        pm2 delete 两个 app（彻底移出进程表）
#   ./ops/oriself.sh health      curl /health + 首页 200 检查
#   ./ops/oriself.sh deploy      git pull → 装依赖 → build → reload → health
#   ./ops/oriself.sh install-startup   让 pm2 开机自启（systemd 集成，需 sudo）
#
# 约定：
#   - 仓库根的 .env 会被 source 进当前 shell，再用 `pm2 start --update-env` 透传。
#   - 日志写到 <repo>/logs/{server,web}.{out,err}.log（已加入 .gitignore）。
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ROOT="$(cd -- "$HERE/.." &>/dev/null && pwd)"
ECO="$HERE/ecosystem.config.cjs"
APPS=("oriself-server" "oriself-web")

mkdir -p "$ROOT/logs"

# ─────────────────────── 前置检查 ────────────────────────

need_pm2() {
  if ! command -v pm2 >/dev/null 2>&1; then
    echo "[err] pm2 未安装。装一下： npm i -g pm2    # 或 pnpm add -g pm2" >&2
    exit 1
  fi
}

need_python() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "[err] 找不到 python3。服务端需要 Python 3.10+。" >&2
    exit 1
  fi
  if ! python3 -c "import oriself_server" >/dev/null 2>&1; then
    echo "[warn] oriself_server 包未安装 ——"
    echo "       (cd $ROOT/server && pip install -e .)"
  fi
}

need_web_build() {
  if [ ! -d "$ROOT/web/.next" ]; then
    echo "[warn] web/.next 不存在，下面会给你跑一次 pnpm build（约 20s）"
    (cd "$ROOT/web" && pnpm install --frozen-lockfile && pnpm build)
  fi
  if [ ! -x "$ROOT/web/node_modules/next/dist/bin/next" ]; then
    echo "[err] 找不到 web/node_modules/next/dist/bin/next —— 先 pnpm install" >&2
    exit 1
  fi
}

load_env() {
  # 仓库根的 .env 优先；没有的话 server/.env；都没有也不阻塞
  local envfile=""
  if [ -f "$ROOT/.env" ]; then
    envfile="$ROOT/.env"
  elif [ -f "$ROOT/server/.env" ]; then
    envfile="$ROOT/server/.env"
  fi
  if [ -n "$envfile" ]; then
    echo "[env] source $envfile"
    set -a
    # shellcheck disable=SC1090
    . "$envfile"
    set +a
  fi
}

# ─────────────────────── 命令实现 ────────────────────────

cmd_start() {
  need_pm2
  need_python
  need_web_build
  load_env
  pm2 start "$ECO" --update-env
  pm2 save --force >/dev/null 2>&1 || true
  pm2 ls
}

cmd_stop() {
  need_pm2
  pm2 stop "$ECO" || true
}

cmd_restart() {
  need_pm2
  load_env
  pm2 restart "$ECO" --update-env
}

cmd_reload() {
  need_pm2
  load_env
  # fork 模式下 reload≈restart，但能触发 --update-env；保留别名便于脚本统一
  pm2 reload "$ECO" --update-env || pm2 restart "$ECO" --update-env
}

cmd_status() {
  need_pm2
  pm2 ls
}

cmd_logs() {
  need_pm2
  local name="${1:-}"
  if [ -n "$name" ]; then
    pm2 logs "$name" --lines 200
  else
    pm2 logs --lines 200
  fi
}

cmd_tail() {
  need_pm2
  local name="${1:-}"
  if [ -n "$name" ]; then
    pm2 logs "$name" --lines 50 --raw
  else
    pm2 logs --lines 50 --raw
  fi
}

cmd_kill() {
  need_pm2
  for app in "${APPS[@]}"; do
    pm2 delete "$app" 2>/dev/null || true
  done
  pm2 save --force >/dev/null 2>&1 || true
}

cmd_health() {
  local shost="${ORISELF_SERVER_HOST:-127.0.0.1}"
  local sport="${ORISELF_SERVER_PORT:-8000}"
  local whost="${ORISELF_WEB_HOST:-127.0.0.1}"
  local wport="${ORISELF_WEB_PORT:-3000}"
  echo "== server (http://$shost:$sport/health) =="
  if ! curl -fsS --max-time 5 "http://$shost:$sport/health"; then
    echo "(server 不响应)"
  fi
  echo
  echo "== web (http://$whost:$wport/) =="
  local code
  code=$(curl -s --max-time 5 -o /dev/null -w "%{http_code}" "http://$whost:$wport/" || echo "000")
  echo "http_code=$code"
}

cmd_deploy() {
  need_pm2
  load_env
  echo "[deploy] git pull --ff-only"
  (cd "$ROOT" && git pull --ff-only)
  echo "[deploy] server : pip install -e ."
  (cd "$ROOT/server" && .venv/bin/pip install -e . >/dev/null)
  echo "[deploy] web    : pnpm install --frozen-lockfile && pnpm build"
  (cd "$ROOT/web" && pnpm install --frozen-lockfile && pnpm build)
  echo "[deploy] pm2 reload --update-env"
  cmd_reload
  echo "[deploy] 等 5s 让两头起来 ..."
  sleep 5
  cmd_health
}

cmd_install_startup() {
  need_pm2
  echo "[startup] 生成 pm2 systemd 单元 —— 接下来它会打印一行 sudo 命令，复制执行即可"
  pm2 startup
  pm2 save --force
}

# ─────────────────────── 入口 ───────────────────────────

usage() {
  cat <<USAGE
OriSelf · 生产端 PM2 控制脚本

用法（在仓库根执行均可）：
  ./ops/oriself.sh start            启动 server + web
  ./ops/oriself.sh stop             停止两个服务
  ./ops/oriself.sh restart          进程级重启
  ./ops/oriself.sh reload           等价 restart 但带 --update-env（改 .env 必用）
  ./ops/oriself.sh status           pm2 ls
  ./ops/oriself.sh logs [name]      pm2 logs --lines 200；name 可选 oriself-server/oriself-web
  ./ops/oriself.sh tail [name]      pm2 logs --lines 50 --raw
  ./ops/oriself.sh kill             pm2 delete 两个 app（进程表也清掉）
  ./ops/oriself.sh health           curl /health + 首页 200 检查
  ./ops/oriself.sh deploy           git pull → pip install → pnpm build → reload → health
  ./ops/oriself.sh install-startup  让 pm2 开机自启（会打印 sudo 命令）

依赖：
  - pm2      npm i -g pm2
  - pnpm     web 构建用
  - python3  + 在 server/ 下跑过一次 pip install -e .

约定：
  - 仓库根 .env（或 server/.env）会被 source 后以 --update-env 透给 PM2。
  - 日志写 <repo>/logs/{server,web}.{out,err}.log（已 .gitignore）。
  - 不对外暴露：server 绑 127.0.0.1:8000，web 绑 127.0.0.1:3000；外面 nginx/caddy 443 → 3000。
USAGE
}

case "${1:-}" in
  start)           shift; cmd_start             "$@" ;;
  stop)            shift; cmd_stop              "$@" ;;
  restart)         shift; cmd_restart           "$@" ;;
  reload)          shift; cmd_reload            "$@" ;;
  status|ls)       shift; cmd_status            "$@" ;;
  logs)            shift; cmd_logs              "$@" ;;
  tail)            shift; cmd_tail              "$@" ;;
  kill)            shift; cmd_kill              "$@" ;;
  health)          shift; cmd_health            "$@" ;;
  deploy)          shift; cmd_deploy            "$@" ;;
  install-startup) shift; cmd_install_startup   "$@" ;;
  ""|-h|--help|help) usage ;;
  *) echo "未知命令：$1"; usage; exit 2 ;;
esac
