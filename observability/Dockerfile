# The telemetry container (NFR4, FR8): traces, logs and dashboards in one container. Jaeger keeps
# the traces, Loki the logs, and Grafana shows both. Each program is copied unchanged from its
# pinned official image: only the packaging is ours. Grafana's image is the base, because it has a
# shell for the start script, and Jaeger and Loki are single static binaries.
FROM cr.jaegertracing.io/jaegertracing/jaeger:2.23.0 AS jaeger
FROM grafana/loki:3.7.9 AS loki

FROM grafana/grafana:13.3.0
COPY --from=jaeger /cmd/jaeger/jaeger-linux /usr/local/bin/jaeger
COPY --from=loki /usr/bin/loki /usr/local/bin/loki
COPY --chmod=0755 start-telemetry.sh /usr/local/bin/start-telemetry

# Loki's files (observability/loki.yaml: path_prefix /loki). The volume mounted here starts with
# this owner, so Loki can write as Grafana's user.
USER root
RUN mkdir /loki && chown 472:0 /loki
USER 472

# Jaeger listens on every interface of the container, as in its own image.
ENV JAEGER_LISTEN_HOST=0.0.0.0
# 3000 Grafana, 3100 Loki (OTLP logs at /otlp), 4318 Jaeger (OTLP/HTTP traces), 16686 Jaeger's UI
EXPOSE 3000 3100 4318 16686
ENTRYPOINT ["/usr/local/bin/start-telemetry"]
