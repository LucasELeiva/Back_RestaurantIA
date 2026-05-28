"""
Capa de acceso a DynamoDB.

Tablas:
  DYNAMODB_TABLE_REGISTROS   → persiste cada predicción (input + output)
  DYNAMODB_TABLE_CLIENTES    → perfil acumulado por cliente (clientes_historico)
  DYNAMODB_TABLE_SEGMENTOS   → medias por segmento para cold start (segmentos_referencia)

Para desarrollo local con DynamoDB Local setear DYNAMODB_ENDPOINT_URL=http://localhost:8000
"""

import os
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.models.schemas import PredictRequest, PredictResponse, FeedbackRequest, ComensalFeedback

logger = logging.getLogger("bistrotech.dynamo_client")

TABLE_REGISTROS = os.getenv("DYNAMODB_TABLE_REGISTROS", "bistrotech-registros")
TABLE_CLIENTES  = os.getenv("DYNAMODB_TABLE_CLIENTES",  "bistrotech-clientes-historico")
TABLE_SEGMENTOS = os.getenv("DYNAMODB_TABLE_SEGMENTOS", "bistrotech-segmentos-referencia")
ENDPOINT_URL    = os.getenv("DYNAMODB_ENDPOINT_URL")     # None → AWS real


def _get_resource():
    kwargs = {"region_name": os.getenv("AWS_REGION", "us-east-1")}
    if ENDPOINT_URL:
        kwargs["endpoint_url"] = ENDPOINT_URL
    return boto3.resource("dynamodb", **kwargs)


def _to_dynamo_value(value):
    if isinstance(value, float):
        return Decimal(str(value))
    return value


# ── Guardar predicción ──────────────────────────────────────────────────────

def save_registro(req: PredictRequest, resp: PredictResponse, ts: str | None = None) -> None:
    """
    Persiste el request + response en la tabla registros.
    Acepta ts externo para que pedido_service comparta el mismo timestamp como clave de feedback.
    """
    try:
        table = _get_resource().Table(TABLE_REGISTROS)
        if ts is None:
            ts = datetime.now(timezone.utc).isoformat()

        for c in req.comensales:
            rec = {
                "id_mesa":            req.id_mesa,
                "persona_ts":         f"{c.id_persona_en_mesa}#{ts}",
                "id_persona_en_mesa": c.id_persona_en_mesa,
                "id_cliente":         c.id_cliente,
                "franja_etaria_persona":    c.franja_etaria_persona.value,
                "cant_acompanantes":        c.cant_acompanantes,
                "motivo_visita":            c.motivo_visita.value,
                "restriccion_alimentaria":  c.restriccion_alimentaria.value,
                "es_repetidor":             c.es_repetidor,
                "visitas_previas":          c.visitas_previas,
                "ticket_promedio_historico": str(c.ticket_promedio_historico)
                                             if c.ticket_promedio_historico is not None else None,
                "orden_de_pedido":    c.orden_de_pedido,
                "dia_semana":         req.dia_semana,
                "franja_horaria":     req.franja_horaria.value,
                "fecha_hora":         ts,
                "mozo_asignado_rank1": resp.mozos_recomendados[0].id_mozo
                                       if resp.mozos_recomendados else None,
                "modelo_version":     resp.modelo_version,
                "latencia_ms":        resp.latencia_ms,
            }
            # DynamoDB no acepta valores None — los removemos
            rec = {k: v for k, v in rec.items() if v is not None}
            table.put_item(Item=rec)

        logger.info("Registro guardado en DynamoDB: id_mesa=%s ts=%s", req.id_mesa, ts)

    except (BotoCoreError, ClientError) as exc:
        logger.warning("No se pudo guardar en DynamoDB: %s", exc)


# ── Historial de cliente ────────────────────────────────────────────────────

def get_cliente_historico(id_cliente: int) -> Optional[dict]:
    """
    Devuelve el perfil acumulado del cliente o None si no existe.
    Campos relevantes: ticket_promedio, visitas_totales, restriccion_detectada
    """
    try:
        table = _get_resource().Table(TABLE_CLIENTES)
        resp = table.get_item(Key={"id_cliente": id_cliente})
        return resp.get("Item")
    except (BotoCoreError, ClientError) as exc:
        logger.warning("Error consultando clientes_historico id=%s: %s", id_cliente, exc)
        return None


# ── Feedback post-servicio ─────────────────────────────────────────────────

def update_feedback(req: FeedbackRequest) -> int:
    """
    Actualiza el registro más reciente de id_mesa + id_persona_en_mesa con los
    campos de feedback del POS. Devuelve 1 si actualizó, 0 si no encontró el registro.
    """
    try:
        from boto3.dynamodb.conditions import Key
        table = _get_resource().Table(TABLE_REGISTROS)

        result = table.query(
            KeyConditionExpression=(
                Key("id_mesa").eq(req.id_mesa) &
                Key("persona_ts").begins_with(f"{req.id_persona_en_mesa}#")
            ),
            ScanIndexForward=False,
            Limit=1,
        )

        items = result.get("Items", [])
        if not items:
            logger.warning("Registro no encontrado para id_mesa=%s id_persona=%s",
                           req.id_mesa, req.id_persona_en_mesa)
            return 0

        persona_ts = items[0]["persona_ts"]

        # Campos obligatorios del feedback
        updates: dict = {
            "id_mozo":       req.id_mozo,
            "id_principal":  req.id_principal,
            "id_bebida":     req.id_bebida,
        }

        # Campos opcionales — solo se agregan si vienen en el request
        optional_fields = [
            "id_entrada", "id_postre",
            "hora_entrega_plato", "hora_retiro_plato",
            "monto_propina", "propina_rate",
            "like_mozo", "like_entrada", "like_principal", "like_postre", "like_bebida",
            "proporcion_dejada_entrada", "proporcion_dejada_principal", "proporcion_dejada_postre",
        ]
        for field in optional_fields:
            val = getattr(req, field)
            if val is not None:
                updates[field] = val.value if hasattr(val, "value") else val

        expr_parts, expr_names, expr_values = [], {}, {}
        for i, (k, v) in enumerate(updates.items()):
            name_ph, val_ph = f"#f{i}", f":v{i}"
            expr_parts.append(f"{name_ph} = {val_ph}")
            expr_names[name_ph] = k
            expr_values[val_ph] = _to_dynamo_value(v)

        table.update_item(
            Key={"id_mesa": req.id_mesa, "persona_ts": persona_ts},
            UpdateExpression="SET " + ", ".join(expr_parts),
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
        )

        logger.info("Feedback actualizado: id_mesa=%s persona_ts=%s", req.id_mesa, persona_ts)
        return 1

    except (BotoCoreError, ClientError, TypeError) as exc:
        logger.warning("Error actualizando feedback: %s", exc)
        return 0


# ── Feedback por clave directa (usado por pedido_service) ──────────────────

def update_feedback_by_key(id_mesa: int, persona_ts: str, c: ComensalFeedback) -> Optional[int]:
    """
    Actualiza un registro usando su clave exacta PK+SK.
    Devuelve id_cliente del registro (puede ser None para walk-ins), o -1 si falló.
    """
    try:
        table = _get_resource().Table(TABLE_REGISTROS)

        updates: dict = {
            "id_mozo":      c.id_mozo,
            "id_principal": c.id_principal,
            "id_bebida":    c.id_bebida,
        }
        for field in [
            "id_entrada", "id_postre",
            "hora_entrega_plato", "hora_retiro_plato",
            "monto_propina", "propina_rate",
            "like_mozo", "like_entrada", "like_principal", "like_postre", "like_bebida",
            "proporcion_dejada_entrada", "proporcion_dejada_principal", "proporcion_dejada_postre",
        ]:
            val = getattr(c, field, None)
            if val is not None:
                updates[field] = val.value if hasattr(val, "value") else val

        expr_parts, expr_names, expr_values = [], {}, {}
        for i, (k, v) in enumerate(updates.items()):
            name_ph, val_ph = f"#f{i}", f":v{i}"
            expr_parts.append(f"{name_ph} = {val_ph}")
            expr_names[name_ph] = k
            expr_values[val_ph] = _to_dynamo_value(v)

        resp = table.update_item(
            Key={"id_mesa": id_mesa, "persona_ts": persona_ts},
            UpdateExpression="SET " + ", ".join(expr_parts),
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
            ReturnValues="ALL_NEW",
        )
        id_cliente = resp.get("Attributes", {}).get("id_cliente")
        logger.info("Feedback by key: id_mesa=%s persona_ts=%s", id_mesa, persona_ts)
        return int(id_cliente) if id_cliente is not None else None
    except (BotoCoreError, ClientError, TypeError) as exc:
        logger.warning("Error en update_feedback_by_key: %s", exc)
        return -1


# ── Actualización de historial post-visita ─────────────────────────────────

def increment_visitas_cliente(id_cliente: int) -> None:
    """Incrementa visitas_totales en clientes_historico al cerrar un pedido."""
    try:
        table = _get_resource().Table(TABLE_CLIENTES)
        table.update_item(
            Key={"id_cliente": id_cliente},
            UpdateExpression="SET visitas_totales = if_not_exists(visitas_totales, :zero) + :uno",
            ExpressionAttributeValues={":zero": 0, ":uno": 1},
        )
        logger.info("Visitas incrementadas: id_cliente=%s", id_cliente)
    except (BotoCoreError, ClientError) as exc:
        logger.warning("Error incrementando visitas id_cliente=%s: %s", id_cliente, exc)


# ── Segmento de referencia (cold start) ────────────────────────────────────

def get_segmento_referencia(franja_etaria: str, franja_horaria: str, motivo: str) -> Optional[dict]:
    """
    Devuelve la media del segmento para imputar ticket_promedio_historico.
    PK = "franja_etaria#franja_horaria#motivo_visita"
    Intenta el segmento específico primero; si no existe devuelve None (el llamador usa media global).
    """
    pk = f"{franja_etaria}#{franja_horaria}#{motivo}"
    try:
        table = _get_resource().Table(TABLE_SEGMENTOS)
        resp = table.get_item(Key={"segmento_pk": pk})
        return resp.get("Item")
    except (BotoCoreError, ClientError) as exc:
        logger.warning("Error consultando segmentos_referencia pk=%s: %s", pk, exc)
        return None
