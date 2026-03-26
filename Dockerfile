FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends openssl gosu \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 shelf

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY static/ ./static/
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 18888

# Entrypoint runs as root for cert generation, then drops to shelf user
CMD ["./entrypoint.sh"]
