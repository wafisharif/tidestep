# TideStep API + web app. Data (DEM, streets, segments) is mounted at /app/data
# from the host; the hourly job runs in the same image (see docker-compose.yml).
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gdal-bin libgdal-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY tidestep ./tidestep
COPY frontend ./frontend
COPY scripts ./scripts
COPY docs ./docs
ENV TIDESTEP_DATA=/app/data
EXPOSE 8000
CMD ["uvicorn", "tidestep.api:app", "--host", "0.0.0.0", "--port", "8000"]
