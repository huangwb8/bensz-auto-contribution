FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY server ./server

RUN python -m pip install --no-cache-dir ".[server]"

EXPOSE 8080
CMD ["uvicorn", "server.app.main:app", "--host", "0.0.0.0", "--port", "8080"]
