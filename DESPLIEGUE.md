# Puesta en producción

## Por qué no sirve InfinityFree

InfinityFree es hosting compartido de **PHP y MySQL**. Esta API necesita
ejecutar Python de forma permanente, además de PostgreSQL y Redis. Allí no hay
manera de conseguirlo: no es un problema de configuración.

Lo que sí encaja es lo contrario — que tu aplicación PHP alojada en InfinityFree
sea **el cliente** que llama a esta API desplegada en otro sitio. Ese es el
montaje que describe esta guía.

```
   InfinityFree                      Render (u otro)
  ┌──────────────┐   HTTPS + API    ┌──────────────────┐
  │  Tu app PHP  │ ───────────────► │  FinAI Gateway   │
  └──────────────┘                  └────────┬─────────┘
                                             │
                                    Neon (PostgreSQL)
                                    Upstash (Redis)
```

---

## Opción recomendada: Render + Neon + Upstash

Las tres tienen plan gratuito permanente y entre las tres cubren lo que hace
falta. Unos 15 minutos.

### 1. Base de datos — [neon.tech](https://neon.tech)

Crea un proyecto y copia la cadena de conexión. **Cámbiale el driver**, porque
esta API usa el asíncrono:

```
postgresql://usuario:clave@host/db      ← lo que te da Neon
postgresql+asyncpg://usuario:clave@host/db   ← lo que hay que poner
```

Quita también `?sslmode=require` si viene: `asyncpg` no entiende ese parámetro.

### 2. Redis — [upstash.com](https://upstash.com)

Crea una base Redis y copia la URL `rediss://...` (con dos eses: va cifrada).

### 3. La API — [render.com](https://render.com)

1. *New* → *Blueprint* y apunta a este repositorio; Render lee `render.yaml`.
2. Rellena en *Environment* las variables marcadas como pendientes:
   `DATABASE_URL`, `REDIS_URL` y la clave del proveedor de visión
   (`GOOGLE_API_KEY` para Gemini, que tiene plan gratuito, o
   `ANTHROPIC_API_KEY` para Claude; basta con una).
3. Copia el valor que Render generó para `API_KEYS`: es la clave con la que se
   autenticará tu aplicación.

Las migraciones se aplican solas al arrancar. Comprueba que vive:

```bash
curl https://TU-APP.onrender.com/api/v1/health/ready
```

Debe responder `database: ok` y `redis: ok`.

---

## Tres cosas que conviene saber del plan gratuito

- **El servicio se duerme** tras un rato sin tráfico. La primera petición
  después tarda cerca de un minuto. Para pruebas es irrelevante; para
  producción, no.
- **El disco es efímero.** Los ficheros de los comprobantes se pierden al
  reiniciar. En modo `inline` da igual para el análisis —ocurre segundos
  después de la subida— pero no podrás reanalizar uno antiguo. La solución
  es implementar `ReceiptStorage` contra S3: el punto de extensión ya está
  en `app/services/storage.py`.
- **La extracción compite con el tráfico web** por la CPU del mismo proceso.
  Con volumen real, pasa a plan de pago y separa el worker de Celery.

---

## Llamar a la API desde PHP (InfinityFree)

**Comprueba primero que tu hosting deja salir.** Sube esto y ábrelo:

```php
<?php
$c = curl_init('https://api.github.com');
curl_setopt_array($c, [CURLOPT_RETURNTRANSFER => true, CURLOPT_USERAGENT => 'test']);
curl_exec($c);
echo curl_errno($c) ? "SIN salida: " . curl_error($c) : "Salida HTTPS correcta";
```

Si da error, el plan gratuito te está bloqueando las conexiones salientes y
tendrás que mover también el cliente a otro alojamiento.

### Crear un pago y subir su comprobante

```php
<?php
const GATEWAY = 'https://TU-APP.onrender.com/api/v1';
const API_KEY = 'la-clave-que-generó-render';

function gateway(string $ruta, array $opciones = []): array {
    $ch = curl_init(GATEWAY . $ruta);
    curl_setopt_array($ch, $opciones + [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT        => 60,   // el plan gratuito arranca despacio
        CURLOPT_HTTPHEADER     => ['X-API-Key: ' . API_KEY],
    ]);
    $cuerpo = curl_exec($ch);
    $estado = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    return ['estado' => $estado, 'datos' => json_decode($cuerpo, true)];
}

// 1. Crear el pago. La clave de idempotencia evita cobrar dos veces si el
//    usuario recarga la página o se corta la red a mitad.
$pago = gateway('/payments', [
    CURLOPT_POST       => true,
    CURLOPT_POSTFIELDS => json_encode([
        'customer_id'  => 'cliente-42',
        'amount_minor' => 128450,          // 1.284,50 € en céntimos
        'currency'     => 'EUR',
        'method'       => 'bank_transfer',
    ]),
    CURLOPT_HTTPHEADER => [
        'X-API-Key: ' . API_KEY,
        'Content-Type: application/json',
        'Idempotency-Key: pedido-4471',
    ],
]);

$referencia = $pago['datos']['reference'];
echo "Pago {$referencia}: {$pago['datos']['status']}\n";

// 2. Subir el justificante que envía el cliente.
$comprobante = gateway('/receipts', [
    CURLOPT_POST       => true,
    CURLOPT_POSTFIELDS => [
        'file' => new CURLFile('/ruta/al/justificante.png', 'image/png'),
        'payment_reference' => $referencia,
    ],
]);

$idComprobante = $comprobante['datos']['id'];

// 3. El análisis es asíncrono: se consulta hasta que termine.
do {
    sleep(3);
    $estado = gateway("/receipts/{$idComprobante}");
} while ($estado['datos']['status'] === 'pending'
      || $estado['datos']['status'] === 'processing');

echo "Conciliación: {$estado['datos']['reconciliation_status']}\n";
```

Con la configuración por defecto, un comprobante que cuadra deja el pago en
`under_review`, esperando que alguien lo confirme desde
`POST /payments/{ref}/review`. Si prefieres que se liquide solo, pon
`RECONCILIATION_AUTO_SETTLE=true`.

La referencia completa de endpoints está en `/docs` de tu despliegue.

---

## Alternativas

| Dónde | Ventaja | Inconveniente |
|---|---|---|
| **Render + Neon + Upstash** | Gratis y permanente; sale del repositorio | El servicio se duerme; disco efímero |
| **Fly.io** | Docker tal cual, sin dormirse | Pide tarjeta |
| **Oracle Cloud Always Free** | Máquina real, `docker compose` completo con worker | Bastante más montaje |
| **VPS (Hetzner, DigitalOcean…)** | Control total, el `docker-compose.yml` ya vale | De pago, y el TLS lo montas tú |
