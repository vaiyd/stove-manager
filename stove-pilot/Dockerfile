# Single image for both services (api and ui differ only by the command, see compose.yaml).
# Python 3.13 is the verified version; the package itself supports Python >= 3.12.

# Stage 1: build the virtualenv (pip and the build backend stay out of the final image).
FROM python:3.13-slim AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /src
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# Non-editable install so the package data listed in MANIFEST.in ships in site-packages.
COPY pyproject.toml MANIFEST.in ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

# Stage 2: runtime.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    STOVE_PILOT_DB_PATH=/data/stove-pilot.sqlite

RUN useradd --system --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin stove \
    && mkdir /data \
    && chown stove:stove /data

COPY --from=build /opt/venv /opt/venv

USER stove
WORKDIR /app
VOLUME ["/data"]
EXPOSE 8710 8711

CMD ["stove-pilot-api"]
