# ADR 0001: Selección explícita de pasarela por pago

- **Estado:** Aceptado
- **Contexto:** La API admite Stripe y Mercado Pago, pero el pago no almacenaba la
  decisión del proveedor y el servicio siempre usaba Stripe.
- **Decisión:** `PaymentCreate.provider` acepta `stripe` o `mercadopago`; Stripe es
  el valor predeterminado para preservar compatibilidad. Se persiste en `payments`
  y se usa al autorizar y devolver.
- **Consecuencia:** Los reintentos deben repetir el mismo cuerpo idempotente, incluido
  el proveedor; una misma clave con proveedor distinto es un conflicto.
