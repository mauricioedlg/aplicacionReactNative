# La compilacion web no recibe .env ni credenciales de Django/Neon.
FROM node:24-bookworm-slim AS web-build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --include=dev

COPY app.json tsconfig.json ./
COPY assets ./assets
COPY public ./public
COPY src ./src

ENV CI=1 \
    EXPO_NO_DOTENV=1 \
    EXPO_PUBLIC_API_SAME_ORIGIN=true

RUN npm run build:web \
    && npm prune --omit=dev \
    && npm cache clean --force


FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NODE_ENV=production \
    PORT=10000 \
    DEBUG=False \
    ENABLE_INTERNAL_PROXY=True \
    TRUST_PROXY_HTTPS=True \
    DJANGO_INTERNAL_URL=http://127.0.0.1:8000

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates libstdc++6 libatomic1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app app

COPY --from=web-build /usr/local/bin/node /usr/local/bin/node
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /app

COPY --chown=app:app --from=web-build /app/node_modules ./node_modules
COPY --chown=app:app --from=web-build /app/dist ./dist
COPY --chown=app:app package.json server.cjs ./
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app backend/servidor ./backend/servidor

# Gunicorn crea su socket de control en el directorio de trabajo de la app.
RUN chown app:app /app

USER app

EXPOSE 10000

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '10000') + '/healthz', timeout=5)"

CMD ["python", "scripts/arrancar-contenedor.py"]
