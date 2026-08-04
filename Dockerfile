FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Signals-only: no trading credentials required at runtime.
# Mount secrets via env / secret manager, never bake into image.
CMD ["python", "main.py", "--mode", "backtest", "--help"]
