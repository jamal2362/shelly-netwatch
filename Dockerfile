# lcd4linux-shelly - schaltet eine Shelly Plug mit dem LCD4Linux-Dashboard.
#
#   docker build -t lcd4linux-shelly .
#   docker run -d --name lcd4linux-shelly --restart unless-stopped \
#       -e DASHBOARD_URL=http://192.168.178.104:8050/api/state \
#       -e SHELLY_HOST=192.168.178.60 \
#       lcd4linux-shelly
#
FROM python:3.13-alpine

LABEL org.opencontainers.image.title="lcd4linux-shelly" \
      org.opencontainers.image.description="Schaltet eine Shelly Plug (Gen2/Gen3) mit dem LCD4Linux-Webdashboard" \
      org.opencontainers.image.source="https://github.com/CE-Repo/LCD4Linux_Shelly" \
      org.opencontainers.image.licenses="MIT"

# tzdata, damit TZ=Europe/Berlin die Zeitstempel im Log wirklich umstellt.
RUN apk add --no-cache tzdata

COPY lcd4linux_shelly.py /app/lcd4linux_shelly.py

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HEARTBEAT_FILE=/tmp/lcd4linux-shelly.heartbeat \
    COMMAND=watch

# Der Dienst braucht keine Rechte: nur ausgehende HTTP-Verbindungen.
RUN adduser -D -H -u 1000 shelly
USER shelly

# Gesund ist der Container, solange der Watcher weiter abfragt - nicht,
# solange das Dashboard online ist.  Ein ausgeschaltetes Kodi ist ein
# gueltiges Ergebnis und kein Fehler.
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python3", "/app/lcd4linux_shelly.py", "health"]

ENTRYPOINT ["python3", "/app/lcd4linux_shelly.py"]
