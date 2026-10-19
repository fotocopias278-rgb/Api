# ADR 0003: Retención mínima de datos personales

- **Estado:** Aceptado
- **Contexto:** Pagos conservan datos opcionales de email e IP que no deben retenerse
  indefinidamente.
- **Decisión:** Una tarea diaria de Celery Beat anonimiza ambos campos tras
  `DATA_RETENTION_DAYS`; el valor predeterminado es 180 días y el mínimo configurable
  es un día.
- **Consecuencia:** Solo se anonimiza email e IP de pagos. Archivos de recibos,
  extracciones, eventos y otros datos requieren reglas de conservación separadas
  antes de alegar cumplimiento normativo.
