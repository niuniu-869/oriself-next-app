FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 系统依赖（curl 用于 healthcheck）
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 依赖层（变动少，利于 cache）
COPY pyproject.toml /app/
RUN pip install --upgrade pip \
    && pip install -e . \
    && pip install uvicorn[standard]

# 代码层
COPY oriself_server /app/oriself_server
COPY tests /app/tests

# 挂载点：/app/skill-repo 由 docker-compose 从宿主机挂进来（指向 submodule）
# 挂载点：/data 存储 SQLite
VOLUME ["/data"]
ENV ORISELF_DB_PATH=/data/oriself.db \
    ORISELF_SKILL_ROOT=/app/skill-repo/skills/oriself

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "oriself_server.main:app", "--host", "0.0.0.0", "--port", "8000"]
