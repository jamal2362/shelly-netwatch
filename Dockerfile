# shelly-netwatch - schaltet eine Shelly, solange ein Dienst im Netz antwortet.
#
#   docker build -t shelly-netwatch .
#   docker run -d --name shelly-netwatch --restart unless-stopped \
#       -e TARGET_URL=http://192.168.178.104:8050/api/state \
#       -e SHELLY_HOST=192.168.178.60 \
#       shelly-netwatch
#
FROM python:3.13-alpine

LABEL org.opencontainers.image.title="shelly-netwatch" \
      org.opencontainers.image.description="Schaltet eine Shelly (Gen2/Gen3) nach der Erreichbarkeit eines Dienstes im Netz" \
      org.opencontainers.image.source="https://github.com/jamal2362/shelly-netwatch" \
      org.opencontainers.image.licenses="MIT"

# tzdata, damit TZ=Europe/Berlin die Zeitstempel im Log wirklich umstellt.
RUN apk add --no-cache tzdata

COPY shelly_netwatch.py /app/shelly_netwatch.py

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HEARTBEAT_FILE=/tmp/shelly-netwatch.heartbeat \
    COMMAND=watch

# Der Dienst braucht keine Rechte: nur ausgehende Verbindungen.
RUN adduser -D -H -u 1000 shelly
USER shelly

# Gesund ist der Container, solange der Watcher weiter abfragt - nicht,
# solange das Ziel online ist.  Ein ausgeschalteter Dienst ist ein
# gueltiges Ergebnis und kein Fehler.
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python3", "/app/shelly_netwatch.py", "health"]

ENTRYPOINT ["python3", "/app/shelly_netwatch.py"]
