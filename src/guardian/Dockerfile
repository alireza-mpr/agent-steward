FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY harness.py trace_view.py main.py ./

USER nobody
CMD ["python", "main.py"]
