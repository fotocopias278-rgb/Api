# Arquitectura

## Componentes

- **FastAPI** expone la API versionada bajo `/api/v1`; dependencias comunes resuelven
  el comercio desde `X-API-Key` y abren una sesión SQLAlchemy por petición.
- **PostgreSQL** guarda pagos, evaluaciones de riesgo, comprobantes, operaciones
  idempotentes, eventos de proveedores, entregas y trabajos.
- **Redis/Celery** transportan análisis de recibos y entregas de webhooks. Celery
  Beat reencola trabajos abandonados y ejecuta la anonimización diaria.
- **Almacenamiento local o S3-compatible** conserva los bytes de los comprobantes.
  El disco local es para desarrollo/pruebas; producción debe compartir almacenamiento
  persistente entre réplicas.
- **Stripe y Mercado Pago** implementan el protocolo común de pasarela. Cada pago
  conserva el proveedor utilizado, de modo que sus reembolsos vuelven al mismo
  proveedor.
- **Proveedor de visión** extrae datos de imágenes/PDF para conciliación.

## Flujos

### Pago

1. Se valida la entrada y se calcula el riesgo con contexto acotado al comercio.
2. El pago, su evaluación y el evento de uso se guardan antes de invocar la pasarela.
3. La clave idempotente se transmite al proveedor. `provider` selecciona Stripe
   (predeterminado) o Mercado Pago.
4. El webhook firmado actualiza el estado y su identificador evita aplicar el mismo
   evento más de una vez.

### Comprobante

1. El endpoint limita la lectura a `receipt_max_bytes + 1`, valida tipo y firma binaria.
2. El archivo se guarda y el registro queda pendiente antes de devolver `202`.
3. Celery procesa el análisis; Beat recupera estados pendientes o bloqueados.

### Webhook saliente

El evento se persiste en `webhook_deliveries` antes de encolarse. El worker firma el
cuerpo y lo entrega con un identificador estable para deduplicación del receptor.
Los reintentos son al menos una vez, no exactamente una vez.

### Conciliación por periodo

`POST /api/v1/reconciliation/jobs` calcula y guarda un informe para el comercio
autenticado y el intervalo `[period_start, period_end)`. El trabajo se devuelve
completado; no se encola. Los informes guardan una instantánea de conteos y
discrepancias.

## Despliegue

Para garantías durables, configurar `RECEIPT_PROCESSING_MODE=celery` y
`WEBHOOK_DELIVERY_MODE=celery`, además de ejecutar un worker y Beat contra la misma
base y broker. Los pasos de Render y los límites de almacenamiento/red se describen
en `DESPLIEGUE.md`.
