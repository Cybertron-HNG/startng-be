# ---- Stage 1: malware gate (build fails here if any signature matches) ----
FROM alpine:3.20 AS malware-scan
WORKDIR /scan
COPY . .
RUN sh malware-scan.sh /scan && touch /scan/.scan-passed

# ---- Stage 2: app ----
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /code
# Forces the scan stage to run (and pass) before anything is installed or copied
COPY --from=malware-scan /scan/.scan-passed /tmp/.scan-passed
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Phone check in a real browser (app/features/phone_check, Ziki #14): docker build --build-arg PHONE_CHECK_BROWSER=1
# adds Playwright's Chromium (about 300 MB) and then PHONE_CHECK_BROWSER=true can be set. Off by default.
ARG PHONE_CHECK_BROWSER=0
COPY requirements-browser.txt .
RUN if [ "$PHONE_CHECK_BROWSER" = "1" ]; then pip install --no-cache-dir -r requirements-browser.txt \
      && python -m playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*; fi
COPY . .
EXPOSE 8000
# Run DB migrations, then start the API
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
