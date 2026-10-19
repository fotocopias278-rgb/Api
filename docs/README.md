# Documentación técnica

- [Contrato OpenAPI](openapi.json): documento generado desde `app.openapi()`.
- [Arquitectura](architecture.md): componentes, integraciones y flujos de ejecución.
- [Modelo de datos](data-model.md): entidades, relaciones y límites de tenencia.
- [Seguridad y operación](security.md): controles actuales y tareas de producción.
- [Runbook de operación](operacion.md): worker, Beat, readiness y diagnóstico de webhooks.
- [Guía de integración](integracion.md): alta, autenticación, idempotencia y firma.
- [ADRs](adrs/): decisiones técnicas registradas.

**Cambio incompatible en `0.2.0-beta.1`:** las devoluciones requieren
`Idempotency-Key`; sin ella, `POST /payments/{reference}/refund` responde `422`.
