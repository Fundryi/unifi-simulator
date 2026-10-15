# syntax=docker/dockerfile:1
FROM python:3.12-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    pip install --no-cache-dir uv==0.12.23
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then export SSL_CERT_FILE=/run/secrets/build_ca; fi; \
    uv sync --frozen --no-dev --no-editable --system-certs

FROM python:3.12-slim
RUN groupadd --gid 10001 emulator && useradd --uid 10001 --gid emulator --no-create-home emulator \
    && mkdir /state && chown emulator:emulator /state
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" MTUNIFI_STATE_DIR=/state PYTHONUNBUFFERED=1
USER 10001:10001
VOLUME ["/state"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["mtunifi", "health"]
ENTRYPOINT ["mtunifi"]
CMD ["run"]
