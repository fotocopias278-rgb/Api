"""Configuración central de la aplicación (variables de entorno)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Ajustes leídos de variables de entorno o del fichero `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Aplicación ---
    project_name: str = "FinAI Gateway"
    environment: Literal["local", "test", "staging", "production"] = "local"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"

    # --- Seguridad ---
    # Lista de claves de API separadas por comas. Sin claves configuradas la API
    # solo arranca en entornos no productivos (ver `validate_runtime`).
    api_keys_raw: str = Field(default="", alias="API_KEYS")

    # --- Persistencia ---
    database_url: str = "postgresql+asyncpg://finai:finai@localhost:5432/finai"
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # --- Caché y colas ---
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None

    # --- Stripe ---
    stripe_api_key: str | None = None
    stripe_webhook_secret: str | None = None

    # --- IA / Visión ---
    anthropic_api_key: str | None = None
    vision_model: str = "claude-opus-5"
    vision_max_tokens: int = 8000
    vision_timeout_seconds: float = 120.0

    # --- Limitación de tasa ---
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 120
    # Ráfaga máxima admitida; por defecto, un minuto entero de cupo.
    rate_limit_burst: int = 0
    # Si Redis cae: True deja pasar el tráfico, False lo rechaza con 429.
    rate_limit_fail_open: bool = True

    # --- Motor de fraude ---
    fraud_review_threshold: int = 40
    fraud_reject_threshold: int = 75
    fraud_high_amount_minor: int = 500_000  # 5.000,00 en la divisa de la operación
    fraud_velocity_max_per_hour: int = 5
    fraud_max_distinct_cards_per_day: int = 3
    fraud_blocked_bins_raw: str = Field(default="", alias="FRAUD_BLOCKED_BINS")

    # --- Conciliación de comprobantes ---
    reconciliation_amount_tolerance_minor: int = 100  # 1,00 de tolerancia
    reconciliation_date_window_hours: int = 72
    # Con `false` (por defecto) un comprobante que cuadra NO liquida el pago:
    # lo deja en revisión para que lo confirme una persona. Actívalo cuando
    # tengas medida la tasa de acierto real del modelo sobre tus comprobantes.
    reconciliation_auto_settle: bool = False
    receipt_max_bytes: int = 10 * 1024 * 1024
    receipt_storage_dir: str = "./var/receipts"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def api_keys(self) -> tuple[str, ...]:
        """Claves de API válidas, normalizadas."""
        return tuple(k.strip() for k in self.api_keys_raw.split(",") if k.strip())

    @computed_field  # type: ignore[prop-decorator]
    @property
    def fraud_blocked_bins(self) -> tuple[str, ...]:
        return tuple(b.strip() for b in self.fraud_blocked_bins_raw.split(",") if b.strip())

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def validate_runtime(self) -> list[str]:
        """Devuelve los problemas de configuración detectados al arrancar.

        En producción se tratan como errores fatales; en el resto de entornos
        solo se registran como avisos para no bloquear el desarrollo local.
        """
        problems: list[str] = []
        if not self.api_keys:
            problems.append("API_KEYS no está configurado: la autenticación rechazará todo.")
        if not self.stripe_api_key:
            problems.append("STRIPE_API_KEY ausente: la pasarela opera en modo simulado.")
        if not self.stripe_webhook_secret:
            problems.append("STRIPE_WEBHOOK_SECRET ausente: no se verificarán los webhooks.")
        if not self.anthropic_api_key:
            problems.append(
                "ANTHROPIC_API_KEY ausente: la extracción de comprobantes usará el stub."
            )
        if not self.rate_limit_enabled:
            problems.append("RATE_LIMIT_ENABLED=false: la API queda sin limitación de tasa.")
        if self.fraud_review_threshold >= self.fraud_reject_threshold:
            problems.append("FRAUD_REVIEW_THRESHOLD debe ser menor que FRAUD_REJECT_THRESHOLD.")
        return problems


@lru_cache
def get_settings() -> Settings:
    """Instancia única de configuración (cacheada por proceso)."""
    return Settings()


settings = get_settings()
