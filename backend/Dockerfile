FROM python:3.12.8-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/workspace/backend

WORKDIR /workspace/backend

COPY backend/requirements.txt ./requirements.txt

# sentence-transformers의 열린 torch 범위가 최신 CUDA 전이를 끌어오지 않도록
# CPU wheel을 먼저 고정한다. 공용 서버는 RAG 추론만 수행하며 GPU runtime을 쓰지 않는다.
RUN pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        torch==2.5.1 \
    && pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/scripts ./scripts
COPY backend/artifacts ./artifacts

# Revision만 바뀔 때 dependency와 source COPY cache는 재사용한다.
ARG SOURCE_REVISION
RUN printf '%s' "$SOURCE_REVISION" | grep -Eq '^[0-9a-f]{40}$'
LABEL org.opencontainers.image.revision=$SOURCE_REVISION
ENV BISTEL_SOURCE_REVISION=$SOURCE_REVISION

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
