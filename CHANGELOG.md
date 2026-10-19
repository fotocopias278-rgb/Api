# Changelog

## 0.2.0-beta.1

### Cambios incompatibles

- `POST /api/v1/payments/{reference}/refund` requiere ahora la cabecera
  `Idempotency-Key`. Las peticiones sin ella reciben `422`; reintenta cada
  devolución con la misma clave para recuperar su resultado sin iniciar un
  segundo movimiento.

### Añadido

- Registro idempotente de reembolsos parciales y acumulado devuelto por pago.
- Informes de conciliación por periodo y aislamiento por comercio.
