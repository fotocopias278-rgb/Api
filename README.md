# 💳 FinAI Gateway — API de Validación de Pagos y Detección de Fraude

API REST asíncrona de alto rendimiento para el procesamiento de pagos, mitigación de fraudes en tiempo real y conciliación automatizada de comprobantes bancarios mediante visión computacional e IA multimodal.

---

## 🚀 Tecnologías Principales

- *Lenguaje:* Python 3.11+
- *Framework:* FastAPI
- *Base de Datos:* PostgreSQL con SQLAlchemy 2.0 (Async) + Alembic
- *Caché y Colas:* Redis + Celery
- *Validación de Esquemas:* Pydantic v2
- *Modelos IA / OCR:* Google Gemini o Anthropic Claude (Structured Outputs)
- *Pasarela de Pago:* Stripe SDK (Sandbox / Webhooks)
- *Pruebas:* PyTest, HTTPX
- *Contenedores:* Docker & Docker Compose

---

## 📁 Arquitectura del Proyecto

```text
finai-gateway/
├── app/
│   ├── api/             # Routers y endpoints (v1)
│   ├── core/            # Configuración, seguridad, dinero, logging
│   ├── db/              # Sesión asíncrona y modelos relacionales
│   ├── schemas/         # Esquemas Pydantic (Request/Response, IA Schemas)
│   ├── services/        # Lógica de negocio (Stripe, Fraud, Vision, Conciliación)
│   └── workers/         # Tareas asíncronas en segundo plano (Celery)
├── alembic/             # Migraciones de base de datos
├── tests/               # Pruebas unitarias y de integración
├── docker-compose.yml   # Servicios locales (PostgreSQL, Redis, API, worker)
├── Dockerfile           # Imagen de producción
└── requirements.txt     # Dependencias de Python
```

---

## ⚡ Puesta en marcha

### Con Docker (recomendado)

```bash
cp .env.example .env      # ajusta al menos API_KEYS
make up                   # levanta db, cache, api y worker
curl http://localhost:8000/api/v1/health
```

La documentación interactiva queda en <http://localhost:8000/docs>.

### En producción

Consulta **[DESPLIEGUE.md](DESPLIEGUE.md)**: guía paso a paso con Render, Neon y
Upstash (plan gratuito), el `render.yaml` listo para usar y ejemplos en PHP para
integrar desde otra aplicación.

Con `RECEIPT_PROCESSING_MODE=inline` la extracción corre dentro del propio
proceso web y todo cabe en un único servicio, sin worker aparte. Es lo que hace
viable el plan gratuito; con volumen real, sepáralo en un worker de Celery.

### En local

```bash
make install              # crea .venv e instala dependencias
cp .env.example .env
make migrate              # aplica el esquema
make run                  # API en http://localhost:8000
make worker               # en otra terminal: procesa comprobantes
```

**Sin credenciales externas la API arranca igualmente:** Stripe entra en modo
simulado y la extracción de comprobantes usa un stub que nunca da por bueno un
documento. Es deliberado — permite desarrollar el flujo completo sin claves, y
deja siempre traza en el log de que no se ha movido dinero real.

---

## 🔐 Autenticación

Todas las rutas salvo `/health*` y el webhook exigen la cabecera `X-API-Key`,
contrastada en tiempo constante contra `API_KEYS`. El webhook de Stripe se
autentica con su firma HMAC, no con la clave de API.

```bash
python -c "from app.core.security import generate_api_key; print(generate_api_key())"
```

---

## 🚦 Limitación de tasa

Cubo de fichas por identidad, respaldado por Redis. Permite ráfagas cortas sin
dejar de acotar el ritmo sostenido — lo que quiere una pasarela de cobro.

El cálculo entero (leer estado, recargar, consumir) vive en **un script Lua
atómico**. No es un detalle de estilo: con llamadas sueltas, dos peticiones
simultáneas leen el mismo saldo y ambas pasan. La prueba de concurrencia lo
verifica — sin el script, 50 de 50 peticiones se cuelan; con él, exactamente 5.

- **Identidad:** la clave de API (se guarda su huella SHA-256, nunca en claro) y,
  para el tráfico sin autenticar, la IP. El limitador corre *antes* de
  autenticar, así que también frena el sondeo de claves.
- **Coste por endpoint:** no todas las peticiones valen lo mismo.

  | Endpoint | Fichas |
  |---|---|
  | `POST /receipts` (extracción con IA) | 10 |
  | `POST /payments` (cobro) | 5 |
  | `POST /fraud/evaluate` | 2 |
  | Resto | 1 |

- **Cabeceras:** `X-RateLimit-Limit` y `X-RateLimit-Remaining` en cada respuesta;
  `Retry-After` (nunca `0`) en los `429`.
- **Exentas:** `/health*`, `/docs`, `/redoc`, `/openapi.json`. Limitar las sondas
  provocaría reinicios en cascada justo cuando hay carga.
- **Si Redis cae:** por defecto se deja pasar el tráfico (`fail-open`) y se
  registra el fallo; quedarse sin contador es malo, pero tumbar toda la pasarela
  porque el contador está caído lo es más. Con `RATE_LIMIT_FAIL_OPEN=false` se
  invierte el criterio.

---

## 📡 Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/v1/health` | Liveness. |
| `GET` | `/api/v1/health/ready` | Readiness: comprueba base de datos y dependencias. |
| `POST` | `/api/v1/payments` | Crea un pago, lo puntúa y lo cobra si el riesgo lo permite. |
| `GET` | `/api/v1/payments` | Lista pagos con filtros y paginación. |
| `GET` | `/api/v1/payments/{ref}` | Detalle del pago con su historial de riesgo. |
| `POST` | `/api/v1/payments/{ref}/refund` | Devolución total o parcial. |
| `POST` | `/api/v1/payments/{ref}/review` | Resuelve manualmente un pago retenido. |
| `POST` | `/api/v1/fraud/evaluate` | Puntúa una operación sin crearla. |
| `POST` | `/api/v1/receipts` | Sube un comprobante y encola su análisis (202). |
| `GET` | `/api/v1/receipts/{id}` | Estado del análisis y datos extraídos. |
| `POST` | `/api/v1/webhooks/stripe` | Recepción idempotente de eventos de Stripe. |

### Ejemplo: crear un pago

```bash
curl -X POST http://localhost:8000/api/v1/payments \
  -H "X-API-Key: $API_KEY" \
  -H "Idempotency-Key: pedido-42" \
  -H "Content-Type: application/json" \
  -d '{"customer_id":"cli_1","amount_minor":2500,"currency":"EUR","card_bin":"424242"}'
```

Un bloqueo por riesgo **no** es un error HTTP: la respuesta sigue siendo `201` y
el veredicto viaja en `status` (`rejected`, `under_review`) junto a `risk_score`
y `risk_decision`. La petición se procesó correctamente; lo que se rechazó fue
la operación.

Reintentar con la misma `Idempotency-Key` devuelve el pago original sin volver a
cobrar; reutilizarla con otro importe da `409`.

### Ejemplo: conciliar un comprobante

```bash
curl -X POST http://localhost:8000/api/v1/receipts \
  -H "X-API-Key: $API_KEY" \
  -F "file=@transferencia.png" \
  -F "payment_reference=pay_..."
```

---

## 🤖 Proveedores de visión

Dos proveedores intercambiables tras el mismo `Protocol`, con idéntico prompt y
esquema. El resto del sistema no sabe cuál respondió.

| Proveedor | Variable | Modelo por defecto | Nota |
|---|---|---|---|
| Google Gemini | `GOOGLE_API_KEY` | `gemini-2.5-flash` | Tiene plan gratuito |
| Anthropic Claude | `ANTHROPIC_API_KEY` | `claude-opus-5` | Requiere crédito prepago |

`VISION_PROVIDER=auto` (por defecto) usa el primero que tenga credenciales;
`anthropic` o `gemini` lo fijan. Nombrar uno sin su clave **no arranca con el
otro a escondidas**: cae al stub y lo registra. Cambiar de modelo por sorpresa en
un sistema que decide sobre dinero sería peor que no extraer nada.

Sin ninguna clave se usa el stub, que marca todo como ilegible: es preferible
dejar la conciliación pendiente a dar por bueno un comprobante que nadie ha leído.

---

## 🛡️ Motor de fraude

Reglas ponderadas sobre un contexto que reúne el importe, la geografía y los
agregados históricos del cliente. La puntuación se satura en 100 y se traduce en
decisión según dos umbrales configurables (`review` 40, `reject` 75).

| Señal | Peso | Se dispara cuando |
|---|---|---|
| `BLOCKED_BIN` | 100 | El BIN está en lista negra (rechazo por sí solo). |
| `CARD_TESTING` | 30 | Varias tarjetas distintas en 24 h. |
| `HIGH_AMOUNT` | 25 | Importe sobre el umbral configurado. |
| `VELOCITY` | 25 | Demasiados pagos en la última hora. |
| `AMOUNT_DEVIATION` | 20 | Importe ≥ 5× la media del cliente. |
| `FAILED_ATTEMPTS` | 20 | 3 o más pagos fallidos en 24 h. |
| `GEO_MISMATCH` | 20 | País de la tarjeta ≠ país de la IP. |
| `DISPOSABLE_EMAIL` | 15 | Dominio de correo desechable. |
| `NEW_CUSTOMER_HIGH_AMOUNT` | 15 | Cliente nuevo con importe elevado. |
| `ODD_HOUR` | 5 | Operación de madrugada (UTC). |

El motor es una función pura: no toca la base de datos ni la red, lo que hace el
scoring reproducible y fácil de auditar. Cada evaluación se guarda como una fila
inmutable en `fraud_assessments`.

---

## 👁️ Extracción y conciliación de comprobantes

`POST /receipts` guarda el fichero, responde `202` y encola el análisis. El
proceso envía la imagen o el PDF al modelo con *structured outputs*, de modo que
la respuesta valida contra `ReceiptExtraction` sin parsear texto libre. Después
se concilia contra el pago:

- **`matched`** — importe, divisa y fecha coinciden dentro de la tolerancia.
- **`mismatched`** — alguna discrepancia, o indicios de manipulación del documento.
- **`duplicate`** — ese número de operación ya justificó otro pago.
- **`unreadable`** — el documento no es legible; requiere revisión manual.

### El veredicto mueve el pago

La conciliación no se queda en un dictamen: actúa sobre el pago.

| Veredicto | Efecto sobre el pago |
|---|---|
| `matched` | Pasa a `under_review` — *propuesto* para liquidar. Con `RECONCILIATION_AUTO_SETTLE=true` y método transferencia, se liquida solo. |
| `duplicate` | `under_review` + señal de fraude `RECEIPT_DUPLICATE` (60). |
| `mismatched` | `under_review` + `RECEIPT_MISMATCH` (25), y `RECEIPT_TAMPERING` (40) si hay indicios de retoque. |
| `unreadable` | No se toca el pago: alguien tiene que mirar el documento. |

Dos límites deliberados:

- **Un comprobante nunca rechaza un pago por sí solo**, ni con la puntuación al
  máximo. La evidencia sale de un OCR; la última palabra es de una persona. La
  puntuación queda registrada para que pueda juzgarla.
- **Un comprobante tardío no deshace lo ya decidido:** un pago liquidado,
  devuelto o rechazado no se ve afectado.

### Tarjeta y transferencia son flujos distintos

| | Tarjeta | Transferencia |
|---|---|---|
| Al crear el pago | Se cobra por la pasarela | **No se toca la pasarela** |
| Qué lo liquida | El webhook de Stripe | El comprobante conciliado |

Una transferencia jamás genera un `PaymentIntent`: el cliente envía el dinero por
su banco, y abrir además un cobro con tarjeta en paralelo sería cobrar dos veces.

Los importes se manejan siempre en unidades mínimas y con `Decimal`; el exponente
por divisa se respeta (JPY sin decimales, KWD con tres).

---

## 🧪 Pruebas

```bash
make test                                        # 159 pruebas sobre SQLite
make test-pg                                     # las mismas contra PostgreSQL
make lint
```

La batería cubre el motor de reglas, la conversión de importes, la conciliación,
el ciclo de vida del pago (idempotencia, revisión, devolución), el webhook
(firma, caducidad, idempotencia), la limitación de tasa y el contrato con la API
de visión.

Los contratos con los proveedores de visión se verifican contra dobles locales
de sus endpoints —uno por proveedor—, así que comprueban la forma exacta de la
petición sin gastar una llamada. Lo que no pueden comprobar es si el modelo
*lee bien* un comprobante: para eso está `make smoke-vision`, que hace una única
llamada real con el proveedor que tengas configurado.

La suite corre sobre SQLite por defecto —rápida y sin dependencias— pero
apuntando `TEST_DATABASE_URL` a un Postgres se ejecuta idéntica contra el motor
real. **Esto no es opcional para las carreras:** SQLite serializa las escrituras,
así que la colisión de dos peticiones simultáneas con la misma clave de
idempotencia solo se reproduce en Postgres.

La limitación de tasa se prueba contra un `redis-server` real y efímero en un
puerto libre (o el que indique `TEST_REDIS_URL`), porque las garantías que
importan —atomicidad del script Lua, TTL, recuperación de `NOSCRIPT`— solo se
comprueban contra el servidor. Sin binario disponible, la fixture cae a
`fakeredis`, que también ejecuta Lua.

La CI (`.github/workflows/ci.yml`) ejecuta lint, la suite sobre ambos motores con
Postgres y Redis reales, `alembic check` —que falla si alguien cambia un modelo y
olvida la migración— y la construcción de la imagen Docker.

---

## ⚙️ Configuración

Todas las variables están documentadas en [`.env.example`](.env.example). En
`ENVIRONMENT=production` una configuración incompleta aborta el arranque en vez
de descubrirse con el primer pago rechazado; en el resto de entornos solo se
registra un aviso.
