"""Enrutador agregado de la versión 1 de la API."""

from fastapi import APIRouter

from app.api.v1.endpoints import admin, fraud, health, payments, receipts, webhooks

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(admin.router)
api_router.include_router(payments.router)
api_router.include_router(fraud.router)
api_router.include_router(receipts.router)
api_router.include_router(webhooks.router)
