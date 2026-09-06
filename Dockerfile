FROM node:22-alpine AS frontend-build

WORKDIR /workspace

COPY package.json package-lock.json ./
RUN npm ci

COPY frontend ./frontend
RUN npm run build:frontend


FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY --from=frontend-build /workspace/app/static/dashboard-app.js ./app/static/dashboard-app.js
COPY --from=frontend-build /workspace/app/static/styles.css ./app/static/styles.css
COPY run_server.py ./
COPY inspect_postgres.py ./
COPY analyze_experiment.py ./

EXPOSE 8765

CMD ["python", "run_server.py"]
