FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MPLCONFIGDIR=/data/.matplotlib
WORKDIR /app
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.txt -c requirements.lock \
    && useradd --create-home --uid 10001 bot \
    && mkdir /data && chown bot:bot /data
COPY --chown=bot:bot app ./app
COPY --chown=bot:bot alembic.ini ./
USER bot
ENV DB_PATH=/data/osrs.sqlite3 BACKUP_DIR=/data/backups
VOLUME /data
CMD ["python", "-m", "app", "run"]
