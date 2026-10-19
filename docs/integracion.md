# Guía de integración

La URL base es `https://<host>/api/v1`. Las rutas de comercio reciben
`X-API-Key`; las de alta administrativa usan `X-Admin-Key`.

## Alta de comercio

Con la clave administrativa, crea el comercio y emite una clave. Guarda el campo
`secret` de la segunda respuesta: solo se muestra al emitirla.

```bash
curl -X POST "$BASE/admin/merchants" \
  -H "X-Admin-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"Tienda Ejemplo"}'

curl -X POST "$BASE/admin/merchants/$MERCHANT_ID/keys" \
  -H "X-Admin-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"produccion"}'
```

No incluyas estas claves en el navegador ni en repositorios. Revoca una clave
expuesta y emite otra.

## Pagos y claves idempotentes

Envía una clave estable por operación lógica en `Idempotency-Key`. Si la conexión
se corta, repite la misma petición con la misma clave; el gateway devuelve el pago
original. No reutilices una clave para otro cliente, importe, divisa, método o
proveedor.

```bash
curl -X POST "$BASE/payments" \
  -H "X-API-Key: $API_KEY" \
  -H "Idempotency-Key: pedido-4471" \
  -H "Content-Type: application/json" \
  -d '{"customer_id":"cliente-42","amount_minor":128450,"currency":"EUR","method":"card","provider":"stripe"}'
```

Los importes usan unidades mínimas de la divisa (por ejemplo, céntimos).

## Devoluciones

`POST /payments/{reference}/refund` requiere también `Idempotency-Key`; sin ella
responde `422`. La misma clave con el mismo pago, importe y motivo recupera la
devolución anterior. Una operación distinta requiere una clave distinta.

```bash
curl -X POST "$BASE/payments/$REFERENCE/refund" \
  -H "X-API-Key: $API_KEY" \
  -H "Idempotency-Key: refund-pedido-4471-1" \
  -H "Content-Type: application/json" \
  -d '{"amount_minor":2500,"reason":"requested_by_customer"}'
```

Omite `amount_minor` para devolver el saldo aún disponible completo. Guarda la
clave por devolución, no solo por pago: un pago puede tener varias devoluciones
parciales.

## Webhooks salientes

Registra el destino desde `POST /webhook-endpoints` y conserva el `secret` que
devuelve la respuesta. Solo se admiten destinos HTTPS públicos.

Cada petición incluye `X-FinAI-Signature` con formato `t=<unix>,v1=<hex>`. La firma
es HMAC-SHA256 del texto `<timestamp>.<cuerpo HTTP original>` con el secreto del
destino. Verifica el cuerpo original, compara en tiempo constante y rechaza
marcas de tiempo fuera de una tolerancia corta:

```python
import hashlib
import hmac
import time

def verificar(cuerpo: bytes, cabecera: str, secreto: str) -> bool:
    partes = dict(parte.split("=", 1) for parte in cabecera.split(","))
    timestamp = int(partes["t"])
    if abs(time.time() - timestamp) > 300:
        return False
    esperado = hmac.new(
        secreto.encode(),
        str(timestamp).encode() + b"." + cuerpo,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(esperado, partes["v1"])
```

Deduplica usando `X-FinAI-Delivery`: los reintentos conservan el identificador y
la entrega es al menos una vez. `X-FinAI-Event` identifica el tipo del evento.
