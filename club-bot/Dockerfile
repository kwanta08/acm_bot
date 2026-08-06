# 鳥人間サークル統合運営 Discord Bot 用イメージ
#
# - SQLite は ./data（コンテナ内 /app/data）に保存する。
#   docker compose の volume マウントで永続化する前提
# - .env はイメージに含めない（.dockerignore で除外）。
#   起動時に docker compose の env_file で注入する
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 依存関係だけ先にコピーしてビルドキャッシュを効かせる
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# SQLite の DB / ログの保存先（volume マウントポイント）
RUN mkdir -p data logs

CMD ["python", "bot.py"]
