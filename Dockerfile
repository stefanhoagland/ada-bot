FROM python:3.12-slim

# Docker CLI so Ada can manage containers through the host's Docker socket.
COPY --from=docker:27.3.1-cli /usr/local/bin/docker /usr/local/bin/docker

RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl procps tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ada ./ada

ENV PYTHONUNBUFFERED=1 \
    ADA_WORKDIR=/workspace \
    TZ=America/New_York
RUN mkdir -p /workspace

CMD ["python", "-m", "ada"]
