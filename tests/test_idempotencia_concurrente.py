"""Carrera de idempotencia: dos peticiones simultáneas con la misma clave.

Es el escenario para el que existe la idempotencia —el cliente reintenta tras un
timeout mientras la primera petición sigue viva— y el que SQLite no puede
reproducir, porque serializa las escrituras.
"""

import asyncio

from app.schemas.payment import PaymentCreate
from app.services.payments import PaymentService

DATOS = PaymentCreate(customer_id="cli_carrera", amount_minor=2_500, currency="EUR")


async def test_dos_peticiones_simultaneas_devuelven_el_mismo_pago(session_factory, merchant):
    async def crear():
        # Sesiones independientes: sin ellas no hay concurrencia real.
        async with session_factory() as session:
            return await PaymentService(session, merchant=merchant).create_payment(
                DATOS, idempotency_key="clave-en-carrera"
            )

    resultados = await asyncio.gather(crear(), crear(), return_exceptions=True)

    fallos = [r for r in resultados if isinstance(r, BaseException)]
    assert not fallos, f"una de las dos peticiones reventó: {fallos}"

    # Ambas deben ver el mismo pago: la idempotencia no puede depender de quién
    # llegue antes.
    assert resultados[0].reference == resultados[1].reference


async def test_no_se_crean_dos_pagos_con_la_misma_clave(session_factory, merchant):
    async def crear():
        async with session_factory() as session:
            return await PaymentService(session, merchant=merchant).create_payment(
                DATOS, idempotency_key="clave-unica-carrera"
            )

    await asyncio.gather(crear(), crear(), return_exceptions=True)

    async with session_factory() as session:
        _, total = await PaymentService(session, merchant=merchant).list_payments(customer_id="cli_carrera")
    assert total == 1, f"se crearon {total} pagos para una sola clave"
