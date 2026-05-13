FROM python:3.12-slim

# uv をインストール（Astral 公式の標準的な手順）
RUN pip install --no-cache-dir uv

WORKDIR /app

# 依存関係を先にコピーしてキャッシュレイヤーを活かす
COPY pyproject.toml ./
COPY uv.lock* ./
RUN uv sync --frozen 2>/dev/null || uv sync

# アプリケーションコード
COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8001

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--reload"]
