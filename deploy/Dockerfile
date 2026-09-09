FROM python:3.12-slim AS source

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY . /app
# The source allowlist excludes local Git history. Workbench project registration
# still requires a repository root; initialize metadata without importing history.
RUN git init --quiet /app
RUN python -m pip install --no-cache-dir -e .

FROM source AS verified
RUN python -X utf8 -m unittest discover -s tests -v \
    && python -X utf8 -m eval.harness --suite blocking \
    && python -c "from pathlib import Path; Path('/tmp/course-checks-passed').write_text('unit tests and blocking Eval passed during image build')"

FROM source AS runtime
# Pull the successful gate into the runtime stage, without its generated databases/reports.
COPY --from=verified /tmp/course-checks-passed /opt/course-checks-passed
EXPOSE 8000 8001
HEALTHCHECK --interval=20s --timeout=3s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/ready', timeout=2)"
CMD ["python", "-X", "utf8", "-m", "workbench.cli", "serve", "--host", "0.0.0.0", "--port", "8000"]
