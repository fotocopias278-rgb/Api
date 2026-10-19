# Runbook de operación

## Procesos de fondo

En producción configura `RECEIPT_PROCESSING_MODE=celery` y
`WEBHOOK_DELIVERY_MODE=celery`. Ejecuta ambos procesos con la misma configuración
de base de datos y Redis que la API:

```bash
celery -A app.workers.celery_app.celery_app worker --loglevel=INFO
celery -A app.workers.celery_app.celery_app beat --loglevel=INFO
```

El worker procesa `receipts.analyze` y `webhooks.deliver`. Beat programa:

| Tarea | Frecuencia | Función |
|---|---:|---|
| `receipts.requeue_stale` | 60 s | Recupera análisis `pending`/`processing` atascados y reintenta `failed` hasta tres veces. |
| `webhooks.requeue_stale` | 60 s | Reencola entregas pendientes elegibles; cada entrega tiene un límite de intentos. |
| `privacy.anonymize_expired` | 24 h | Anonimiza PII vencida y elimina entregas de webhook ya entregadas. |

Comprueba que Beat y el worker estén activos, compartan `REDIS_URL` y
`DATABASE_URL`, y que los logs de arranque confirmen el modo `celery`. La cola no
sustituye el almacenamiento persistente de comprobantes: usa S3-compatible si hay
más de una réplica.

## Readiness y Redis

`/api/v1/health/ready` devuelve `503` si Redis no responde. El rate limiter puede
estar configurado fail-open, pero con una sola réplica el orquestador puede quitar
el servicio completo del balanceador mientras la readiness falle. Revisa el estado
del servicio, la conectividad a Upstash y la recuperación de la sonda cuando Redis
vuelva.

## Webhooks salientes que no llegan

1. Revisa `webhook_endpoints`: URL HTTPS pública, destino activo y filtro
   `event_types` compatible con el evento.
2. Consulta la entrega en `webhook_deliveries`: `attempts`, `status_code`,
   `last_error`, `delivered_at` y `last_enqueued_at`.
3. Si no se intentó entregar, comprueba el worker, Beat, Redis y los logs
   `webhook.enqueue_failed` / `webhook.requeue_failed`.
4. Si hay error de destino rechazado, confirma que el DNS resuelve solo a IPs
   públicas. La aplicación rechaza IPs privadas, no sigue redirecciones y no usa
   proxies del entorno; el firewall de salida también debe bloquear rangos internos.
5. Para HTTP 4xx/5xx o timeout, revisa la disponibilidad del receptor, el
   `X-FinAI-Delivery` para deduplicar y la verificación de `X-FinAI-Signature`.

Las entregas son al menos una vez. El receptor debe verificar la firma sobre el
cuerpo HTTP sin modificar y deduplicar por `X-FinAI-Delivery`.
