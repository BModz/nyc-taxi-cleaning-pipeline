FROM python:3.13-slim

WORKDIR /app
COPY requirements-ingest.txt .
RUN pip install --no-cache-dir -r requirements-ingest.txt

COPY ingest_data.py .

ENTRYPOINT ["python", "ingest_data.py"]
