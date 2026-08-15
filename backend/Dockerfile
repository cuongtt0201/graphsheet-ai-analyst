# Build context is the repo root (see docker-compose.yml) because the backend
# imports model_eval/ as a sibling directory (app/config.py: MODEL_EVAL_DIR).
FROM python:3.11-slim

WORKDIR /srv/backend

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY model_eval /srv/model_eval

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
