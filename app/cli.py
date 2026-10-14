"""Utilidades de línea de comandos.

Resuelve el problema del huevo y la gallina: para emitir la primera clave de un
comercio hace falta una clave de administración, y para probar que todo
funciona hace falta un comercio. Esto lo hace en un comando, contra la base de
datos configurada.

    python -m app.cli crear-comercio "Mi Tienda"
    python -m app.cli emitir-clave <merchant_id>
    python -m app.cli listar-comercios
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from app.db.session import SessionFactory
from app.services.merchants import MerchantService


async def crear_comercio(nombre: str) -> int:
    async with SessionFactory() as session:
        servicio = MerchantService(session)
        comercio = await servicio.create_merchant(nombre)
        emitida = await servicio.issue_key(comercio.id, name="inicial")

    print(f"Comercio creado: {comercio.name}")
    print(f"  id:    {comercio.id}")
    print(f"  clave: {emitida.secreto}")
    print("\nGuarda la clave ahora: no se puede recuperar.")
    return 0


async def emitir_clave(merchant_id: str, nombre: str) -> int:
    async with SessionFactory() as session:
        emitida = await MerchantService(session).issue_key(uuid.UUID(merchant_id), name=nombre)
    print(f"Clave emitida ({emitida.api_key.prefix}…): {emitida.secreto}")
    print("\nGuárdala ahora: no se puede recuperar.")
    return 0


async def listar_comercios() -> int:
    async with SessionFactory() as session:
        comercios = await MerchantService(session).list_merchants()
    if not comercios:
        print("No hay comercios dados de alta.")
        return 0
    for c in comercios:
        estado = "activo" if c.is_active else "inactivo"
        print(f"{c.id}  {c.name:30} {estado:9} {len(c.api_keys)} clave(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="Administración de FinAI Gateway.")
    sub = parser.add_subparsers(dest="comando", required=True)

    p_crear = sub.add_parser("crear-comercio", help="Crea un comercio y emite su primera clave.")
    p_crear.add_argument("nombre")

    p_clave = sub.add_parser("emitir-clave", help="Emite una clave para un comercio existente.")
    p_clave.add_argument("merchant_id")
    p_clave.add_argument("--nombre", default="default")

    sub.add_parser("listar-comercios", help="Lista los comercios dados de alta.")

    args = parser.parse_args(argv)
    if args.comando == "crear-comercio":
        return asyncio.run(crear_comercio(args.nombre))
    if args.comando == "emitir-clave":
        return asyncio.run(emitir_clave(args.merchant_id, args.nombre))
    return asyncio.run(listar_comercios())


if __name__ == "__main__":
    sys.exit(main())
