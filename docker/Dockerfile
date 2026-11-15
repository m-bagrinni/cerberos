# syntax=docker/dockerfile:1.7
# ---- build stage ----
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# только манифесты — кэш слоёв
COPY pyproject.toml uv.lock ./

# создаём venv и ставим зависимости
RUN uv venv /opt/venv --python 3.12 && \
    . /opt/venv/bin/activate && \
    uv pip install --no-cache -r pyproject.toml

# ---- runtime stage ----
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_ENV=dev

# Docker CLI (docker.io даёт только daemon, нужен docker-ce-cli)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    snmp \
    fping \
    iputils-ping \
    iproute2 \
    arping \
    tcpdump \
    libcap2-bin \
    curl \
    tini \
    ca-certificates \
    gnupg \
    libreoffice-writer-nogui \
    libreoffice-calc-nogui \
    libreoffice-impress-nogui \
    fonts-dejavu-core \
    fonts-liberation \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli \
    && setcap cap_net_raw,cap_net_admin=eip /usr/bin/tcpdump \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# код (в dev перекрывается volume-ом)
COPY . /app

EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
