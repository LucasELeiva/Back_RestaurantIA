import os
import uuid
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from boto3.dynamodb.conditions import Attr

from app.models.schemas import (
    PedidoCreate, PedidoResponse, PedidoFeedbackRequest, PedidoFeedbackResponse,
    EstadoPedido, PredictRequest,
)
from app.services.ml_client import run_inference
from app.services.dynamo_client import save_registro, update_feedback_by_key, increment_visitas_cliente

logger = logging.getLogger("bistrotech.pedido_service")

TABLE_PEDIDOS = os.getenv("DYNAMODB_TABLE_PEDIDOS", "bistrotech-pedidos")
ENDPOINT_URL  = os.getenv("DYNAMODB_ENDPOINT_URL")


def _get_table():
    kwargs = {"region_name": os.getenv("AWS_REGION", "us-east-1")}
    if ENDPOINT_URL:
        kwargs["endpoint_url"] = ENDPOINT_URL
    return boto3.resource("dynamodb", **kwargs).Table(TABLE_PEDIDOS)


def _to_response(item: dict) -> PedidoResponse:
    return PedidoResponse(
        id_pedido=item["id_pedido"],
        id_mesa=int(item["id_mesa"]),
        estado=item["estado"],
        fecha_hora=item["fecha_hora"],
        mozos_recomendados=json.loads(item["mozos_recomendados"]),
        recomendaciones_por_comensal=json.loads(item["recomendaciones_por_comensal"]),
        modelo_version=item["modelo_version"],
        latencia_ms=int(item["latencia_ms"]),
    )


def create_pedido(id_mesa: int, req: PedidoCreate) -> PedidoResponse:
    ts = datetime.now(timezone.utc).isoformat()

    predict_req = PredictRequest(
        id_mesa=id_mesa,
        comensales=req.comensales,
        dia_semana=req.dia_semana,
        franja_horaria=req.franja_horaria,
    )
    predict_resp = run_inference(predict_req)
    save_registro(predict_req, predict_resp, ts=ts)

    id_pedido = str(uuid.uuid4())
    item = {
        "id_pedido":                    id_pedido,
        "id_mesa":                      id_mesa,
        "estado":                       EstadoPedido.activo.value,
        "fecha_hora":                   ts,
        "mozos_recomendados":           json.dumps([m.model_dump() for m in predict_resp.mozos_recomendados]),
        "recomendaciones_por_comensal": json.dumps([r.model_dump() for r in predict_resp.recomendaciones_por_comensal]),
        "modelo_version":               predict_resp.modelo_version,
        "latencia_ms":                  predict_resp.latencia_ms,
    }
    try:
        _get_table().put_item(Item=item)
        logger.info("Pedido guardado: id_pedido=%s id_mesa=%s", id_pedido, id_mesa)
    except (BotoCoreError, ClientError) as exc:
        logger.warning("No se pudo guardar pedido en DynamoDB: %s", exc)

    return _to_response(item)


def get_pedido(id_pedido: str) -> Optional[PedidoResponse]:
    try:
        resp = _get_table().get_item(Key={"id_pedido": id_pedido})
        item = resp.get("Item")
        return _to_response(item) if item else None
    except (BotoCoreError, ClientError) as exc:
        logger.warning("Error consultando pedido %s: %s", id_pedido, exc)
        return None


def list_pedidos(id_mesa: int) -> list[PedidoResponse]:
    try:
        resp = _get_table().scan(FilterExpression=Attr("id_mesa").eq(id_mesa))
        items = sorted(resp.get("Items", []), key=lambda i: i["fecha_hora"], reverse=True)
        return [_to_response(i) for i in items]
    except (BotoCoreError, ClientError) as exc:
        logger.warning("Error listando pedidos id_mesa=%s: %s", id_mesa, exc)
        return []


def submit_feedback(id_pedido: str, req: PedidoFeedbackRequest) -> Optional[PedidoFeedbackResponse]:
    try:
        pedido = _get_table().get_item(Key={"id_pedido": id_pedido}).get("Item")
    except (BotoCoreError, ClientError):
        return None

    if not pedido:
        return None

    id_mesa = int(pedido["id_mesa"])
    ts      = pedido["fecha_hora"]
    actualizados = 0

    for c in req.comensales:
        persona_ts = f"{c.id_persona_en_mesa}#{ts}"
        id_cliente = update_feedback_by_key(id_mesa, persona_ts, c)
        if id_cliente != -1:
            actualizados += 1
            if id_cliente is not None:
                increment_visitas_cliente(id_cliente)

    if actualizados:
        try:
            _get_table().update_item(
                Key={"id_pedido": id_pedido},
                UpdateExpression="SET estado = :e",
                ExpressionAttributeValues={":e": EstadoPedido.cerrado.value},
            )
        except (BotoCoreError, ClientError) as exc:
            logger.warning("No se pudo cerrar pedido %s: %s", id_pedido, exc)

    logger.info("Feedback pedido=%s registros=%s", id_pedido, actualizados)
    return PedidoFeedbackResponse(
        ok=actualizados > 0,
        id_pedido=id_pedido,
        registros_actualizados=actualizados,
    )
