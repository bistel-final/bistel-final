FROM node:22.14.0-alpine AS build

WORKDIR /workspace/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN node --version && npm ci

COPY frontend/ ./

ARG VITE_API_BASE_URL=/api
ARG VITE_USE_MOCK=false
ARG VITE_USE_MOCK_DETECTION=false
ARG VITE_USE_MOCK_AGENT=false
ARG VITE_USE_MOCK_KNOWLEDGE=false
ARG VITE_USE_MOCK_ANALYTICS=false
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
ENV VITE_USE_MOCK=${VITE_USE_MOCK}
ENV VITE_USE_MOCK_DETECTION=${VITE_USE_MOCK_DETECTION}
ENV VITE_USE_MOCK_AGENT=${VITE_USE_MOCK_AGENT}
ENV VITE_USE_MOCK_KNOWLEDGE=${VITE_USE_MOCK_KNOWLEDGE}
ENV VITE_USE_MOCK_ANALYTICS=${VITE_USE_MOCK_ANALYTICS}

RUN test "$VITE_API_BASE_URL" = "/api" \
    && test "$VITE_USE_MOCK" = "false" \
    && test "$VITE_USE_MOCK_DETECTION" = "false" \
    && test "$VITE_USE_MOCK_AGENT" = "false" \
    && test "$VITE_USE_MOCK_KNOWLEDGE" = "false" \
    && test "$VITE_USE_MOCK_ANALYTICS" = "false" \
    && npm run build \
    && ! grep -R -q -E "localhost:8000|127\\.0\\.0\\.1:8000" dist/assets \
    && grep -R -q -F "/api" dist/assets

FROM nginx:1.27.3-alpine

COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /workspace/frontend/dist /usr/share/nginx/html

# Revision만 바뀔 때 npm ci와 production bundle cache는 재사용한다.
ARG SOURCE_REVISION
RUN printf '%s' "$SOURCE_REVISION" | grep -Eq '^[0-9a-f]{40}$'
LABEL org.opencontainers.image.revision=$SOURCE_REVISION

EXPOSE 80
