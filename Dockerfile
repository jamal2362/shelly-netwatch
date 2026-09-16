# shelly-netwatch - switches a Shelly while a service on the network answers.
#
#   docker build -t shelly-netwatch .
#   docker run -d --name shelly-netwatch --restart unless-stopped \
#       -e TARGET_URL=http://192.168.178.104:8050/api/state \
#       -e SHELLY_HOST=192.168.178.60 \
#       shelly-netwatch
#
FROM python:3.13-alpine

LABEL org.opencontainers.image.title="shelly-netwatch" \
      org.opencontainers.image.description="Switches a Shelly (Gen2/Gen3) by the reachability of a service on the network" \
      org.opencontainers.image.source="https://github.com/jamal2362/shelly-netwatch" \
      org.opencontainers.image.licenses="MIT"

# tzdata, so that TZ=Europe/Berlin really moves the timestamps in the log.
RUN apk add --no-cache tzdata

COPY shelly_netwatch.py /app/shelly_netwatch.py

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HEARTBEAT_FILE=/tmp/shelly-netwatch.heartbeat \
    COMMAND=watch

# The service needs no privileges: outgoing connections and nothing else.
RUN adduser -D -H -u 1000 shelly
USER shelly

# The container is healthy while the watcher keeps polling - not while the
# target is online.  A switched-off target is a valid result, not an error.
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python3", "/app/shelly_netwatch.py", "health"]

ENTRYPOINT ["python3", "/app/shelly_netwatch.py"]
