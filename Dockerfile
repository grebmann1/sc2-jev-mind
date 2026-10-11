FROM --platform=$BUILDPLATFORM debian:bookworm-slim AS emulator
RUN apt-get update && apt-get install -y --no-install-recommends qemu-user-static \
    && rm -rf /var/lib/apt/lists/*

FROM python:3.12-slim
COPY --from=emulator /usr/bin/qemu-x86_64-static /usr/local/bin/sc2-qemu
COPY --from=ghcr.io/astral-sh/uv:0.9.18 /uv /uvx /usr/local/bin/
RUN apt-get update && apt-get install -y --no-install-recommends \
    libstdc++6 libglib2.0-0 libx11-6 libxrender1 libxext6 libsdl2-2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev
ENV SC2PATH=/sc2 PYTHONUNBUFFERED=1
ENTRYPOINT ["uv", "run", "--no-sync", "sc2mind"]
CMD ["--offline", "--no-realtime"]
