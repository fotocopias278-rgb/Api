# Modelo de datos

## Entidades principales

- `merchants`: organización propietaria de los recursos; todas las consultas de API
  se limitan por `merchant_id`.
- `api_keys`: credenciales por comercio; se conserva el hash, no la clave en claro.
- `payments`: importe en unidades mínimas de la divisa, estado, proveedor, referencia
  opaca y campos opcionales de cliente/riesgo.
- `fraud_assessments`: historial inmutable de cada evaluación y sus señales.
- `refund_operations`: clave idempotente por comercio, importe solicitado/reservado,
  estado y referencia de la devolución del proveedor.
- `receipts`: metadatos, estado de extracción/conciliación, relación opcional con un
  pago y URI del archivo.
- `webhook_events`: eventos entrantes, únicos por `(provider, event_id)`.
- `webhook_endpoints` y `webhook_deliveries`: destinos por comercio y outbox de
  notificaciones salientes.
- `reconciliation_jobs`: informe completado con periodo, contadores y discrepancias.
- `usage_events`: hechos de uso facturables asociados a un comercio.

## Aislamiento

Los pagos, reembolsos, recibos, destinos, entregas e informes llevan `merchant_id`.
Las rutas consultables por clientes filtran por el comercio autenticado. Los eventos
entrantes no tienen propietario directo porque el proveedor envía una cuenta común;
se correlacionan por identificador de pago. Para detectar eventos de pasarela huérfanos
en un informe, el payload firmado debe identificar el comercio en `metadata.merchant_id`.

## Importes

`amount_minor` y `refunded_amount_minor` son enteros expresados en unidades mínimas
ISO 4217; la conversión usa el exponente de moneda, no una división flotante fija.
