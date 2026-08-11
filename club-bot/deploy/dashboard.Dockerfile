# ダッシュボード（FastAPI）用イメージ
#
# bot 本体とは別イメージ・別プロセス。discord.py や matplotlib は含めない
# （dashboard/requirements.txt のみをインストールする）。
#
# ビルドコンテキストは club-bot/ を想定:
#   docker build -f deploy/dashboard.Dockerfile ..
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 依存関係だけ先にコピーしてビルドキャッシュを効かせる。
# ダッシュボードは DB ドライバも必要（bot 本体の requirements には
# 含まれるが、この イメージには入らないため個別に入れる）
COPY dashboard/requirements.txt dashboard-requirements.txt
RUN pip install --no-cache-dir -r dashboard-requirements.txt \
    && pip install --no-cache-dir "asyncpg>=0.29.0" "aiosqlite>=0.19.0"

# ダッシュボードが参照する共有モジュール（リポジトリ層・DB・権限）
COPY dashboard ./dashboard
COPY repositories ./repositories
COPY services ./services
COPY utils ./utils
COPY config.py ./config.py

RUN mkdir -p data logs

EXPOSE 8000

# 公開は Caddy 経由。コンテナ内では全 IF で待ち受ける
CMD ["uvicorn", "dashboard.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
