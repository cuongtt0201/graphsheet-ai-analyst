# Build context is the repo root (see docker-compose.yml) because the backend
# imports model_eval/ as a sibling directory (app/config.py: MODEL_EVAL_DIR).
FROM python:3.11-slim

WORKDIR /srv/backend

# Install from the lock, not from requirements.txt: the lock pins transitive
# packages too. A rebuild that resolves dependencies freshly is how google-genai
# jumped to 2.19 and broke every LLM call without a line of our code changing.
# requirements.txt is copied alongside it as the record of what we depend on
# directly and why each version was chosen.
COPY backend/requirements.txt backend/requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt

COPY backend/app ./app
COPY model_eval /srv/model_eval

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
