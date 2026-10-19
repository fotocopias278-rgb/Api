"""Instancia de Celery para el procesamiento en segundo plano."""

from __future__ import annotations

from celery import Celery

from app.core.config import settings
from app.core.logging import configure_logging

configure_logging(settings.log_level)

celery_app = Celery(
    "finai_gateway",
    broker=settings.broker_url,
    backend=settings.result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Se confirma la tarea al terminar, no al recibirla: si el worker muere a
    # mitad de una extracción, el comprobante vuelve a la cola.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_retry_delay=30,
    task_time_limit=600,
    task_soft_time_limit=540,
    result_expires=86_400,
    beat_schedule={
        "requeue-stale-receipt-analysis": {
            "task": "receipts.requeue_stale",
            "schedule": 60.0,
        },
        "requeue-stale-webhook-deliveries": {
            "task": "webhooks.requeue_stale",
            "schedule": 60.0,
        },
    },
)
