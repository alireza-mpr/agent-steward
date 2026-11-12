#!/bin/sh
# Starts the three programs of the telemetry container (observability/Dockerfile). When any one of
# them stops, the others are stopped too, and the container exits with an error: a crash shows as a
# stopped container, not as one that half works. docker compose stop sends TERM, which stops all three.
set -u

jaeger &
jaeger_pid=$!
loki -config.file=/etc/loki/loki.yaml &
loki_pid=$!
/run.sh &   # Grafana's own start script, from its image
grafana_pid=$!

stop_all() {
    kill "$jaeger_pid" "$loki_pid" "$grafana_pid" 2>/dev/null
    wait
    exit "$1"
}
trap 'stop_all 0' TERM INT

# BusyBox sh has no working "wait -n", so check every 2 seconds that all three still run.
while kill -0 "$jaeger_pid" 2>/dev/null && kill -0 "$loki_pid" 2>/dev/null && kill -0 "$grafana_pid" 2>/dev/null; do
    sleep 2
done
echo "start-telemetry: one of jaeger, loki and grafana stopped, so the container stops" >&2
stop_all 1
