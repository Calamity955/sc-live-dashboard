# SC Live Dashboard — image di produzione
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Rome

WORKDIR /app

# Dipendenze di sistema minime (tzdata per timezone Europe/Rome)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Solo il codice — i dati e la config arrivano via volume e .env
COPY backend ./backend
COPY frontend ./frontend
COPY samples ./samples

# I dati persistenti vivono in /app/data (montato come volume)
RUN mkdir -p /app/data/csv

EXPOSE 8765

# Avvio uvicorn — non in modalità reload, no access log
CMD ["python", "-m", "uvicorn", "backend.main:app", \
     "--host", "0.0.0.0", "--port", "8765", \
     "--no-access-log", "--proxy-headers"]
