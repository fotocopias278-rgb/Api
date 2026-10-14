"""Verifica un despliegue en marcha, de punta a punta.

Uso:
    python scripts/smoke_deploy.py https://tu-app.onrender.com --api-key TU_CLAVE
    API_KEY=... python scripts/smoke_deploy.py https://tu-app.onrender.com

Recorre el camino completo —salud, pago, subida de comprobante, extracción con
IA y conciliación— contra el servicio real y dice qué funciona y qué no. No
necesita acceso a la plataforma: solo la URL y la clave de API del servicio.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import httpx

COMPROBANTE = Path(__file__).resolve().parent.parent / "tests/fixtures/comprobante_ejemplo.png"
IMPORTE = 128_450  # 1.284,50 €, lo que dice el comprobante de ejemplo

VERDE, ROJO, AMARILLO, NEGRITA, FIN = "\033[32m", "\033[31m", "\033[33m", "\033[1m", "\033[0m"


class Informe:
    def __init__(self) -> None:
        self.fallos = 0
        self.avisos = 0

    def ok(self, texto: str, detalle: str = "") -> None:
        print(f"  {VERDE}✓{FIN} {texto}" + (f"  {detalle}" if detalle else ""))

    def error(self, texto: str, detalle: str = "") -> None:
        self.fallos += 1
        print(f"  {ROJO}✗{FIN} {texto}" + (f"  {detalle}" if detalle else ""))

    def aviso(self, texto: str, detalle: str = "") -> None:
        self.avisos += 1
        print(f"  {AMARILLO}!{FIN} {texto}" + (f"  {detalle}" if detalle else ""))


def barra(titulo: str) -> None:
    print(f"\n{NEGRITA}{titulo}{FIN}\n" + "─" * 66)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica un despliegue de FinAI Gateway.")
    parser.add_argument("url", help="URL base del servicio, p. ej. https://x.onrender.com")
    parser.add_argument("--api-key", default=os.environ.get("API_KEY"),
                        help="Clave de un comercio. Si falta, se crea uno con --admin-key.")
    parser.add_argument("--admin-key", default=os.environ.get("ADMIN_KEY"),
                        help="Clave de administración, para dar de alta un comercio de prueba.")
    parser.add_argument("--timeout", type=float, default=90.0,
                        help="El plan gratuito tarda en despertar.")
    args = parser.parse_args()

    if not args.api_key and not args.admin_key:
        print("✗ Pasa --api-key (comercio) o --admin-key (para crear uno de prueba).")
        return 1

    base = args.url.rstrip("/") + "/api/v1"
    cabeceras = {"X-API-Key": args.api_key or ""}
    inf = Informe()

    with httpx.Client(timeout=args.timeout, follow_redirects=True) as cli:
        # Si solo hay clave de administración, se crea un comercio desechable:
        # así el verificador no exige haber dado de alta nada antes.
        if not args.api_key:
            barra("0. Se da de alta un comercio de prueba")
            alta = cli.post(
                f"{base}/admin/merchants",
                json={"name": "Verificación de despliegue"},
                headers={"X-Admin-Key": args.admin_key},
            )
            if alta.status_code != 201:
                inf.error(f"no se pudo crear el comercio ({alta.status_code})", alta.text[:160])
                return 1
            emision = cli.post(
                f"{base}/admin/merchants/{alta.json()['id']}/keys",
                json={"name": "verificacion"},
                headers={"X-Admin-Key": args.admin_key},
            )
            args.api_key = emision.json()["secret"]
            inf.ok("comercio y clave creados", alta.json()["id"])
            cabeceras["X-API-Key"] = args.api_key
        # --- Salud ---
        barra("1. El servicio responde")
        try:
            inicio = time.perf_counter()
            r = cli.get(f"{base}/health")
            demora = time.perf_counter() - inicio
        except httpx.HTTPError as exc:
            inf.error("No se pudo contactar con el servicio", str(exc))
            return 1

        if r.status_code == 200:
            inf.ok(f"health responde en {demora:.1f} s", f"versión {r.json().get('version')}")
            if demora > 20:
                inf.aviso("Tardó mucho: el servicio estaba dormido (plan gratuito)")
        else:
            inf.error(f"health devolvió {r.status_code}")
            return 1

        estado = cli.get(f"{base}/health/ready").json().get("checks", {})
        for servicio, esperado in (("database", "ok"), ("redis", "ok")):
            valor = estado.get(servicio)
            (inf.ok if valor == esperado else inf.error)(f"{servicio}: {valor}")
        vision_stub = estado.get("vision") == "stub"
        if vision_stub:
            inf.aviso("vision: stub — sin clave de IA, no se extraerá nada")
        else:
            inf.ok(f"vision: {estado.get('vision')}")

        # --- Autenticación ---
        barra("2. La autenticación protege la API")
        sin_clave = cli.get(f"{base}/payments")
        (inf.ok if sin_clave.status_code == 401 else inf.error)(
            f"sin clave devuelve {sin_clave.status_code}", "(se espera 401)"
        )
        con_clave = cli.get(f"{base}/payments", headers=cabeceras)
        if con_clave.status_code == 200:
            inf.ok("la clave de API es válida")
        else:
            inf.error(f"con la clave devuelve {con_clave.status_code}",
                      "revisa API_KEYS en la plataforma")
            return 1
        if "X-RateLimit-Limit" in con_clave.headers:
            inf.ok("limitación de tasa activa", f"{con_clave.headers['X-RateLimit-Limit']}/min")
        else:
            inf.aviso("sin cabeceras de limitación: ¿RATE_LIMIT_ENABLED=false?")

        # --- Pago ---
        barra("3. Se crea un pago por transferencia")
        pago = cli.post(
            f"{base}/payments",
            headers={**cabeceras, "Idempotency-Key": f"smoke-{int(time.time())}"},
            json={"customer_id": "smoke-test", "amount_minor": IMPORTE,
                  "currency": "EUR", "method": "bank_transfer"},
        )
        if pago.status_code != 201:
            inf.error(f"la creación devolvió {pago.status_code}", pago.text[:160])
            return 1
        datos = pago.json()
        referencia = datos["reference"]
        inf.ok(f"pago {referencia}", f"{datos['amount']} EUR, estado {datos['status']}")
        (inf.ok if datos["provider_payment_id"] is None else inf.error)(
            "no se llamó a la pasarela", "(correcto en una transferencia)"
        )

        # --- Comprobante ---
        barra("4. Se sube el comprobante y se analiza")
        if not COMPROBANTE.exists():
            inf.error(f"falta el fichero de ejemplo {COMPROBANTE}")
            return 1
        with COMPROBANTE.open("rb") as fichero:
            subida = cli.post(
                f"{base}/receipts", headers=cabeceras,
                files={"file": (COMPROBANTE.name, fichero, "image/png")},
                data={"payment_reference": referencia},
            )
        if subida.status_code != 202:
            inf.error(f"la subida devolvió {subida.status_code}", subida.text[:200])
            return 1
        receipt_id = subida.json()["id"]
        inf.ok("comprobante aceptado (202)")

        estado_final = None
        for _ in range(30):
            time.sleep(2)
            actual = cli.get(f"{base}/receipts/{receipt_id}", headers=cabeceras).json()
            if actual["status"] not in ("pending", "processing"):
                estado_final = actual
                break
        if estado_final is None:
            inf.error("el análisis no terminó en 60 s",
                      "sin worker, ¿está RECEIPT_PROCESSING_MODE=inline?")
            return 1

        if estado_final["status"] == "extracted":
            inf.ok("extracción completada")
            extraido = estado_final.get("extraction") or {}
            comprobaciones = {
                "importe": (extraido.get("amount"), "1284.50"),
                "divisa": (extraido.get("currency"), "EUR"),
                "nº operación": (extraido.get("operation_number"), "TRF-2026-0417-88231"),
            }
            for etiqueta, (obtenido, esperado) in comprobaciones.items():
                if obtenido == esperado:
                    inf.ok(f"{etiqueta}: {obtenido!r}")
                elif vision_stub:
                    # Sin proveedor de IA no hay nada que extraer: es lo
                    # esperado, no un fallo del despliegue.
                    inf.aviso(f"{etiqueta}: sin extraer", "(stub)")
                else:
                    inf.error(f"{etiqueta}: {obtenido!r}", f"(se esperaba {esperado!r})")
        else:
            inf.error(f"la extracción acabó en {estado_final['status']}",
                      estado_final.get("error_message") or "")

        # --- Conciliación ---
        barra("5. Se concilia contra el pago")
        veredicto = estado_final["reconciliation_status"]
        if veredicto == "matched":
            inf.ok("veredicto: matched")
        elif veredicto == "unreadable":
            inf.aviso("veredicto: unreadable", "sin proveedor de IA configurado")
        else:
            inf.error(f"veredicto: {veredicto}",
                      "; ".join(estado_final.get("reconciliation_notes") or []))

        final = cli.get(f"{base}/payments/{referencia}", headers=cabeceras).json()
        print(f"  → el pago quedó en {NEGRITA}{final['status']}{FIN}")

        # --- Consumo ---
        barra("6. El consumo queda medido para facturar")
        consumo = cli.get(f"{base}/usage", headers=cabeceras)
        if consumo.status_code == 200:
            totales = consumo.json()["totals"]
            inf.ok("se contabilizó el cobro", f"{totales.get('payment_created')} unidad(es)")
            inf.ok("se contabilizó el comprobante", f"{totales.get('receipt_uploaded')}")
            if not vision_stub:
                (inf.ok if totales.get("vision_extraction") else inf.error)(
                    "se contabilizó la extracción", str(totales.get("vision_extraction"))
                )
        else:
            inf.error(f"/usage devolvió {consumo.status_code}")

    barra("Resultado")
    if inf.fallos:
        extra = f", {inf.avisos} aviso(s)" if inf.avisos else ""
        print(f"{ROJO}{inf.fallos} fallo(s){FIN}{extra}")
        return 1
    print(f"{VERDE}Todo correcto{FIN}" + (f", con {inf.avisos} aviso(s)" if inf.avisos else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
