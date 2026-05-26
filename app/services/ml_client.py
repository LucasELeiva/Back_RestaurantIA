"""
Servicio de inferencia — llama al modelo en AWS SageMaker.

Configuración vía variables de entorno:
  ML_BACKEND          → "sagemaker" | "local"  (default: local)
  SAGEMAKER_ENDPOINT  → nombre del endpoint en SageMaker
  AWS_REGION          → región AWS (default: us-east-1)

Para desarrollo local corre el fallback determinístico sin credenciales AWS.
"""

import os
import time
import json
import logging
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.models.schemas import PredictRequest, PredictResponse
from app.services.dynamo_client import get_cliente_historico, get_segmento_referencia

logger = logging.getLogger("bistrotech.ml_client")

ML_BACKEND = os.getenv("ML_BACKEND", "local")
SAGEMAKER_ENDPOINT = os.getenv("SAGEMAKER_ENDPOINT", "bistrotech-endpoint")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
MODELO_VERSION = os.getenv("MODELO_VERSION", "v1.0")


# ── Enriquecimiento desde clientes_historico ───────────────────────────────

def enrich_comensales(req: PredictRequest) -> None:
    """
    Resuelve es_repetidor, visitas_previas y ticket_promedio_historico
    consultando clientes_historico por id_cliente (DNI).
    Muta el objeto req directamente — debe llamarse antes de run_inference.
    Walk-ins (id_cliente=None) → es_repetidor=False, visitas_previas=0.
    """
    for c in req.comensales:
        if c.id_cliente is not None:
            perfil = get_cliente_historico(c.id_cliente)
            if perfil:
                visitas = int(perfil.get("visitas_totales", 0))
                c.es_repetidor = visitas > 0
                c.visitas_previas = visitas
                if c.ticket_promedio_historico is None and perfil.get("ticket_promedio") is not None:
                    c.ticket_promedio_historico = float(perfil["ticket_promedio"])
                logger.debug("Cliente id=%s enriquecido: visitas=%s repetidor=%s",
                             c.id_cliente, visitas, c.es_repetidor)
            else:
                c.es_repetidor = False
                c.visitas_previas = 0
                logger.debug("Cliente id=%s sin historial — cold start", c.id_cliente)
        else:
            c.es_repetidor = False
            c.visitas_previas = 0


# ── Imputación de ticket via DynamoDB ──────────────────────────────────────

def _resolver_ticket(comensal, req) -> float | None:
    """
    Orden de prioridad para imputar ticket_promedio_historico cuando es None:
      1. Historial del cliente en DynamoDB (clientes_historico)
      2. Media del segmento (segmentos_referencia) — cold start
      3. None → el modelo maneja el fallback internamente
    """
    if comensal.id_cliente is not None:
        perfil = get_cliente_historico(comensal.id_cliente)
        if perfil and perfil.get("ticket_promedio") is not None:
            logger.debug("Ticket imputado desde clientes_historico id=%s", comensal.id_cliente)
            return float(perfil["ticket_promedio"])

    segmento = get_segmento_referencia(
        comensal.franja_etaria_persona.value,
        req.franja_horaria.value,
        comensal.motivo_visita.value,
    )
    if segmento and segmento.get("ticket_promedio_segmento") is not None:
        logger.debug("Ticket imputado desde segmento %s#%s#%s",
                     comensal.franja_etaria_persona.value,
                     req.franja_horaria.value,
                     comensal.motivo_visita.value)
        return float(segmento["ticket_promedio_segmento"])

    return None


# ── Helpers de feature engineering ─────────────────────────────────────────

def _dia_a_ciclico(dia: int) -> dict:
    """Convierte dia_semana a seno/coseno para que domingo y lunes sean 'cercanos'."""
    import math
    angulo = 2 * math.pi * dia / 7
    return {"dia_semana_sin": round(math.sin(angulo), 6),
            "dia_semana_cos": round(math.cos(angulo), 6)}


def _build_payload(req: PredictRequest) -> dict:
    """
    Construye el payload que espera el modelo.
    Nunca incluye columnas de feedback (like_*, propina_rate, etc.) — solo features.
    """
    ciclico = _dia_a_ciclico(req.dia_semana)
    comensales_payload = []

    for c in req.comensales:
        ticket = c.ticket_promedio_historico

        if ticket is None:
            ticket = _resolver_ticket(c, req)

        comensales_payload.append({
            "id_persona_en_mesa": c.id_persona_en_mesa,
            "franja_etaria_persona": c.franja_etaria_persona.value,
            "cant_acompanantes": c.cant_acompanantes,
            "viene_solo": c.cant_acompanantes == 0,
            "motivo_visita": c.motivo_visita.value,
            "restriccion_alimentaria": c.restriccion_alimentaria.value,
            "es_repetidor": c.es_repetidor,
            "visitas_previas_log1p": round(__import__("math").log1p(c.visitas_previas), 6),
            "ticket_promedio_historico": ticket,
            "orden_de_pedido": c.orden_de_pedido,
            **ciclico,
            "franja_horaria": req.franja_horaria.value,
        })

    return {
        "id_mesa": req.id_mesa,
        "comensales": comensales_payload,
    }


# ── Cliente SageMaker ───────────────────────────────────────────────────────

def _call_sagemaker(payload: dict) -> dict:
    client = boto3.client("sagemaker-runtime", region_name=AWS_REGION)
    response = client.invoke_endpoint(
        EndpointName=SAGEMAKER_ENDPOINT,
        ContentType="application/json",
        Body=json.dumps(payload),
    )
    return json.loads(response["Body"].read())


# ── Fallback local (desarrollo sin credenciales AWS) ───────────────────────

def _fallback_local(req: PredictRequest) -> dict:
    """
    Respuesta determinística para desarrollo local.
    Replica exactamente la estructura que devuelve el modelo real.
    """
    mozos = [
        {"id_mozo": i, "propina_rate_esperado": round(0.5 - i * 0.001, 4), "rank": i}
        for i in range(1, 9)
    ]

    recomendaciones = []
    for c in req.comensales:
        recomendaciones.append({
            "id_persona_en_mesa": c.id_persona_en_mesa,
            "entrada":    [{"id_plato": p, "score": round(0.13 - i * 0.005, 4), "rank": i + 1}
                           for i, p in enumerate([4, 8, 5])],
            "principal":  [{"id_plato": p, "score": round(0.12 - i * 0.01,  4), "rank": i + 1}
                           for i, p in enumerate([18, 17, 13])],
            "postre":     [{"id_plato": p, "score": round(0.22 - i * 0.02,  4), "rank": i + 1}
                           for i, p in enumerate([24, 22, 23])],
            "bebida":     [{"id_plato": p, "score": round(0.21 - i * 0.005, 4), "rank": i + 1}
                           for i, p in enumerate([30, 28, 29])],
        })

    return {
        "id_mesa": req.id_mesa,
        "mozos_recomendados": mozos,
        "recomendaciones_por_comensal": recomendaciones,
        "modelo_version": MODELO_VERSION,
    }


# ── Punto de entrada público ────────────────────────────────────────────────

def run_inference(req: PredictRequest) -> PredictResponse:
    enrich_comensales(req)
    t0 = time.monotonic()

    payload = _build_payload(req)

    if ML_BACKEND == "sagemaker":
        try:
            logger.info("Llamando SageMaker endpoint: %s", SAGEMAKER_ENDPOINT)
            raw = _call_sagemaker(payload)
        except (BotoCoreError, ClientError) as exc:
            logger.error("Error SageMaker: %s — usando fallback local", exc)
            raw = _fallback_local(req)
    else:
        logger.debug("ML_BACKEND=local — usando fallback determinístico")
        raw = _fallback_local(req)

    latencia_ms = int((time.monotonic() - t0) * 1000)

    return PredictResponse(
        id_mesa=raw["id_mesa"],
        mozos_recomendados=raw["mozos_recomendados"],
        recomendaciones_por_comensal=raw["recomendaciones_por_comensal"],
        modelo_version=raw.get("modelo_version", MODELO_VERSION),
        latencia_ms=latencia_ms,
    )
