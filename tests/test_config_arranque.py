"""Validación de la configuración al arrancar."""

from app.core.config import Settings, get_settings


def ajustes(**overrides) -> Settings:
    base = get_settings().model_dump(exclude={"api_keys", "fraud_blocked_bins"})
    return Settings(**{**base, **overrides})


def test_sin_claves_de_api_es_un_error():
    errores, _ = ajustes(api_keys_raw="").validate_runtime()
    assert any("API_KEYS" in e for e in errores)


def test_stripe_sin_secreto_de_webhook_es_un_error():
    """Cobrar de verdad sin poder verificar las notificaciones es inaceptable."""
    errores, _ = ajustes(
        stripe_api_key="sk_live_x", stripe_webhook_secret=None
    ).validate_runtime()
    assert any("STRIPE_WEBHOOK_SECRET" in e for e in errores)


def test_sin_stripe_solo_es_un_aviso():
    """Un gateway que solo cobre por transferencia no necesita Stripe."""
    errores, avisos = ajustes(
        stripe_api_key=None, stripe_webhook_secret=None
    ).validate_runtime()
    assert not errores
    assert any("simulado" in a for a in avisos)


def test_umbrales_invertidos_son_un_error():
    errores, _ = ajustes(
        fraud_review_threshold=90, fraud_reject_threshold=50
    ).validate_runtime()
    assert any("THRESHOLD" in e for e in errores)


def test_el_modo_inline_avisa_pero_no_bloquea():
    errores, avisos = ajustes(receipt_processing_mode="inline").validate_runtime()
    assert not errores
    assert any("inline" in a for a in avisos)


def test_una_configuracion_completa_no_da_errores():
    errores, _ = ajustes(
        stripe_api_key="sk_live_x",
        stripe_webhook_secret="whsec_x",
        anthropic_api_key="sk-ant-x",
    ).validate_runtime()
    assert not errores
