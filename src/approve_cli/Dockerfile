FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY approve.py ./
# The shared OpenTelemetry setup (src/shared, an additional build context in docker-compose.yml).
COPY --from=shared *.py ./

USER nobody
CMD ["python", "approve.py"]
