FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY allow_list.py approval.py rules.py graph_reader.py server.py ./

USER nobody
CMD ["python", "server.py"]
