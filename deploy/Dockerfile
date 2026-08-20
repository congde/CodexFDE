FROM python:3.12-slim

WORKDIR /app
COPY . /app
RUN python -X utf8 -m unittest discover -s tests -v \
    && python -X utf8 -m eval.harness --suite blocking

EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=3s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/ready', timeout=2)"
CMD ["python", "-X", "utf8", "-m", "workbench.cli", "serve", "--host", "0.0.0.0", "--port", "8000"]
