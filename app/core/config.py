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
        # Permite construir los ajustes por nombre de campo además de por
        # alias, que es como los reconstruyen los tests a partir de model_dump.
        populate_by_name=True,
    )

    # --- Aplicación ---
    project_name: str = "FinAI Gateway"
    environment: Literal["local", "test", "staging", "production"] = "local"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"

    # --- Seguridad ---
    # Claves de ADMINISTRACIÓN, separadas por comas: dan de alta comercios y
    # emiten sus claves. Las credenciales de los comercios viven en la base de
    # datos, no aquí: añadir un cliente no puede exigir un redespliegue.
    admin_api_keys_raw: str = Field(default="", alias="ADMIN_API_KEYS")

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
    # `auto` usa el primer proveedor con credenciales; también puede fijarse.
    vision_provider: Literal["auto", "anthropic", "gemini"] = "auto"
    anthropic_api_key: str | None = None
    google_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash"
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

    # --- Webhooks salientes ---
    webhook_timeout_seconds: float = 10.0

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
    # `local` guarda en disco (desarrollo); `s3` en cualquier servicio
    # compatible: AWS, Cloudflare R2, Backblaze B2, MinIO.
    receipt_storage_backend: Literal["local", "s3"] = "local"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None  # vacío = AWS; si no, R2/B2/MinIO
    s3_region: str = "auto"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    # `celery`: la extracción va a la cola y la ejecuta un worker aparte (lo
    # adecuado en producción, porque aísla el gasto de IA del tráfico web).
    # `inline`: la ejecuta el propio proceso web tras responder. Permite
    # desplegar todo como un único servicio, que es lo que ofrecen los planes
    # gratuitos, a costa de compartir CPU con las peticiones.
    receipt_processing_mode: Literal["celery", "inline"] = "celery"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def admin_api_keys(self) -> tuple[str, ...]:
        """Claves de administración válidas, normalizadas."""
        return tuple(k.strip() for k in self.admin_api_keys_raw.split(",") if k.strip())

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

    def validate_runtime(self) -> tuple[list[str], list[str]]:
        """Revisa la configuración al arrancar. Devuelve `(errores, avisos)`.

        Los errores abortan el arranque en producción; los avisos solo se
        registran. La distinción importa: desplegar sin Stripe es legítimo
        —un gateway que solo cobre por transferencia no lo necesita—, pero
        desplegar con Stripe y sin secreto de webhook no lo es.
        """
        errores: list[str] = []
        avisos: list[str] = []

        if not self.admin_api_keys:
            errores.append(
                "ADMIN_API_KEYS no está configurado: no se podrían dar de alta comercios."
            )
        if self.fraud_review_threshold >= self.fraud_reject_threshold:
            errores.append("FRAUD_REVIEW_THRESHOLD debe ser menor que FRAUD_REJECT_THRESHOLD.")
        if self.stripe_api_key and not self.stripe_webhook_secret:
            errores.append(
                "STRIPE_API_KEY sin STRIPE_WEBHOOK_SECRET: se cobrarían pagos reales sin "
                "poder verificar las notificaciones de la pasarela."
            )

        if not self.stripe_api_key:
            avisos.append("STRIPE_API_KEY ausente: la pasarela opera en modo simulado.")
        if not (self.anthropic_api_key or self.google_api_key):
            avisos.append(
                "Sin ANTHROPIC_API_KEY ni GOOGLE_API_KEY: la extracción usará el stub."
            )
        if not self.rate_limit_enabled:
            avisos.append("RATE_LIMIT_ENABLED=false: la API queda sin limitación de tasa.")
        if self.receipt_storage_backend == "s3" and not (
            self.s3_bucket and self.s3_access_key_id and self.s3_secret_access_key
        ):
            errores.append(
                "RECEIPT_STORAGE_BACKEND=s3 sin bucket o credenciales: no se podrían "
                "guardar los comprobantes."
            )
        if self.receipt_storage_backend == "local":
            avisos.append(
                "RECEIPT_STORAGE_BACKEND=local: los comprobantes se pierden al reiniciar "
                "y no se comparten entre réplicas."
            )
        if self.receipt_processing_mode == "inline":
            avisos.append(
                "RECEIPT_PROCESSING_MODE=inline: la extracción comparte proceso con el "
                "tráfico web."
            )

        return errores, avisos


@lru_cache
def get_settings() -> Settings:
    """Instancia única de configuración (cacheada por proceso)."""
    return Settings()


settings = get_settings()
