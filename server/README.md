# oriself-server

FastAPI 后端。对话循环、guardrails、多 provider LLM 适配。

## 开发

```bash
cd server
pip install -e ".[dev]"

# mock provider，不需要 API key
uvicorn oriself_server.main:app --reload  # :8000
```

测试:

```bash
pytest
pytest --cov=oriself_server
```

## API

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/letters` | 创建新信件（会话） |
| `POST` | `/letters/{id}/turn` | 发送一轮 |
| `GET`  | `/letters/{id}/state` | 当前状态 |
| `GET`  | `/letters/{id}/result` | 收敛后的结果（含 issue slug） |
| `GET`  | `/issues/{slug}` | 公开报告元数据 |
| `GET`  | `/issues/{slug}/render` | 完整 HTML（iframe 嵌入） |
| `PATCH`| `/issues/{slug}/publish` | 切换公开 / 私有 |
| `GET`  | `/health` | 健康检查 |

Swagger UI: `http://localhost:8000/docs`

## 环境变量

见 `../.env.example`。
