# ADR 0002: Informes de conciliación por periodo

- **Estado:** Aceptado
- **Contexto:** El esquema ya contenía `reconciliation_jobs`, pero no había rutas que
  generaran o devolvieran informes.
- **Decisión:** `POST /api/v1/reconciliation/jobs` calcula sincrónicamente una foto del
  intervalo semiabierto `[period_start, period_end)` y la persiste completada;
  `GET` solo devuelve trabajos del comercio autenticado.
- **Consecuencia:** Los informes pequeños tienen respuesta inmediata y estable. Para
  intervalos de gran volumen, habrá que mover el cálculo a un worker y añadir límites
  de periodo/paginación.
