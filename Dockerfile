FROM debian:trixie-slim

LABEL org.opencontainers.image.source="https://github.com/tolecnal/pydebuprep-portal" \
      org.opencontainers.image.description="A portal that keeps pydebuprep reports in one place" \
      org.opencontainers.image.licenses="MIT"

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Everything the portal runs, pydebuprep included, comes from the hashed lock file;
# so does setuptools, which builds pydebuprep and the portal without build isolation.
WORKDIR /build
COPY requirements-build.txt requirements.txt ./
RUN python3 -m venv /opt/portal \
 && /opt/portal/bin/pip install --no-cache-dir --require-hashes -r requirements-build.txt \
 && /opt/portal/bin/pip install --no-cache-dir --require-hashes --no-build-isolation \
      -r requirements.txt
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN /opt/portal/bin/pip install --no-cache-dir --no-deps --no-build-isolation . \
 && rm -rf /build

RUN useradd --system --uid 10001 --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin portal
COPY --chmod=0755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

USER portal
WORKDIR /
ENV PATH=/opt/portal/bin:$PATH PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD ["python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
ENTRYPOINT ["docker-entrypoint.sh"]
