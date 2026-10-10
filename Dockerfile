FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY server.py simulator.py ./
COPY frontend ./frontend

RUN mkdir -p /app/data

EXPOSE 8765

CMD ["python", "server.py"]
