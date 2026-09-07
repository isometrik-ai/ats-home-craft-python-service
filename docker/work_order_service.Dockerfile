FROM python:3.13-slim

ARG BUILD_ENV=production
ARG BUILD_VERSION=1.0.0

RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

COPY apps/work_order_service/requirements.txt ./work-order-requirements.txt
RUN pip install --no-cache-dir -r work-order-requirements.txt

COPY . .

RUN chown -R appuser:appuser /app

USER appuser

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV BUILD_ENV=${BUILD_ENV}
ENV BUILD_VERSION=${BUILD_VERSION}

EXPOSE 5001

CMD ["uvicorn", "apps.work_order_service.app.main:app", "--host", "0.0.0.0", "--port", "5001", "--log-level", "info", "--access-log"]
