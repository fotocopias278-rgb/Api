# Seguridad y operación

## Controles implementados

- Claves de API de comercio verificadas mediante hash y aislamiento por `merchant_id`.
- Idempotencia de pagos sobre la petición normalizada completa y de reembolsos
  persistida por comercio.
- Verificación de firmas de webhooks entrantes, tolerancia temporal y deduplicación
  por proveedor e identificador de evento.
- URLs salientes limitadas a HTTPS, comprobación de DNS/IP pública, sin proxies de
  entorno ni redirecciones automáticas.
- Límite de tamaño de comprobante, verificación de firma binaria y almacenamiento
  local confinado a su raíz o S3-compatible.
- Tarea periódica `privacy.anonymize_expired` que, al vencer `DATA_RETENTION_DAYS`
  (180 días por defecto), borra `email` e `ip_address` de pagos, elimina entregas de
  webhook ya entregadas y anonimiza las claves `email`, `ip` e `ip_address` de los
  payloads retenidos de entregas y eventos entrantes.

## Límites importantes

- La defensa SSRF resuelve DNS antes de enviar, pero httpx vuelve a resolver el host
  al conectar: existe una ventana TOCTOU para DNS rebinding. No sustituye reglas de
  firewall/egress que bloqueen redes privadas y endpoints de metadata.
- La retención cubre email/IP de pagos y payloads de webhooks; no borra imágenes de
  recibos, datos extraídos ni otros campos personales. Los ficheros y OCR de
  comprobantes requieren una política de conservación y borrado independiente.
- La entrega saliente es al menos una vez; los receptores deben deduplicar por
  `X-FinAI-Delivery`.
- El límite de archivo del endpoint no reemplaza un límite de tamaño del cuerpo HTTP
  en el proxy/plataforma.
- `inline` procesa en el proceso web y no ofrece el mismo aislamiento/reintentos que
  Celery. En producción durable se requieren worker y Beat activos.

## Lista de salida a producción

1. Definir el plan de retención con asesoría legal y confirmar `DATA_RETENTION_DAYS`.
2. Configurar PostgreSQL con backups y ensayar una restauración.
3. Configurar bucket privado, credenciales de mínimo privilegio y lifecycle/backup.
4. Ejecutar worker Celery y Beat; comprobar alarmas, logs y recuperación de tareas.
5. Aplicar límites de request y firewall de salida en la plataforma.
6. Usar secretos de producción/Sandbox separados; probar cobros, reembolsos y
   webhooks con cada proveedor antes de abrir tráfico.
7. Rotar credenciales expuestas en pruebas y establecer un procedimiento de rotación.

Este documento describe controles de ingeniería; no constituye certificación PCI-DSS
ni una declaración de cumplimiento legal.
