FROM python:3.13-slim

# Standard library only: no packages to install.
WORKDIR /app
COPY rules.py graph_reader.py main.py ./

USER nobody
CMD ["python", "main.py"]
