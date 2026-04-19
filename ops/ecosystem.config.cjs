/* eslint-env node */
/**
 * OriSelf · PM2 ecosystem
 *
 * 两个进程：
 *   oriself-server · FastAPI  @ 127.0.0.1:8000  (python3 -m uvicorn)
 *   oriself-web    · Next.js  @ 127.0.0.1:3000  (node node_modules/next/...)
 *
 * 路径统一以本文件位置推导，无需硬编码部署目录。
 * 环境变量由 `ops/oriself.sh` 在启动前 source `<repo>/.env`（如果存在），
 * 再透过 `--update-env` 透传给 PM2。
 *
 * 调用方式：
 *   pm2 start ops/ecosystem.config.cjs --update-env
 *   pm2 reload ops/ecosystem.config.cjs --update-env
 *   pm2 stop  ops/ecosystem.config.cjs
 */
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const LOGS = path.join(ROOT, "logs");

// 允许通过 env 覆盖端口，方便多实例 / 灰度
const SERVER_HOST = process.env.ORISELF_SERVER_HOST || "127.0.0.1";
const SERVER_PORT = process.env.ORISELF_SERVER_PORT || "8000";
const WEB_HOST = process.env.ORISELF_WEB_HOST || "127.0.0.1";
const WEB_PORT = process.env.ORISELF_WEB_PORT || "3000";

// 让 web 通过 rewrite 打到本机的 server —— 与 next.config.mjs 契约对齐
const API_INTERNAL_URL =
  process.env.API_INTERNAL_URL || `http://${SERVER_HOST}:${SERVER_PORT}`;

module.exports = {
  apps: [
    {
      name: "oriself-server",
      cwd: path.join(ROOT, "server"),
      script: "python3",
      args: [
        "-m",
        "uvicorn",
        "oriself_server.main:app",
        "--host",
        SERVER_HOST,
        "--port",
        SERVER_PORT,
        "--proxy-headers",
      ].join(" "),
      interpreter: "none", // pm2 不要再套一层 node
      exec_mode: "fork",
      instances: 1,
      autorestart: true,
      max_restarts: 10,
      min_uptime: "10s",
      kill_timeout: 5000,
      listen_timeout: 10000,
      restart_delay: 1500,
      env: {
        PYTHONUNBUFFERED: "1",
      },
      out_file: path.join(LOGS, "server.out.log"),
      error_file: path.join(LOGS, "server.err.log"),
      merge_logs: true,
      time: true,
    },
    {
      name: "oriself-web",
      cwd: path.join(ROOT, "web"),
      // 直接指向本地 next 二进制，避免 pnpm 再 fork 一个壳进程让 pm2 管不动
      script: path.join(ROOT, "web", "node_modules", "next", "dist", "bin", "next"),
      args: `start -p ${WEB_PORT} -H ${WEB_HOST}`,
      interpreter: "node",
      exec_mode: "fork",
      instances: 1,
      autorestart: true,
      max_restarts: 10,
      min_uptime: "10s",
      kill_timeout: 5000,
      listen_timeout: 10000,
      restart_delay: 1500,
      env: {
        NODE_ENV: "production",
        PORT: WEB_PORT,
        HOSTNAME: WEB_HOST,
        API_INTERNAL_URL,
      },
      out_file: path.join(LOGS, "web.out.log"),
      error_file: path.join(LOGS, "web.err.log"),
      merge_logs: true,
      time: true,
    },
  ],
};
