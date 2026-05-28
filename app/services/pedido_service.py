import os
import uuid
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from boto3.dynamodb.conditions import Attr, Key

from app.models.schemas import (
    PedidoCreate, PedidoResponse, PedidoFeedbackRequest, PedidoFeedbackResponse,
    EstadoPedido, PredictRequest, Comensal, PedidoMesaCreate,
    ComensalPedidoCreate, ComensalPedidoResponse, PedidoEstadoUpdate,
    ComensalCodigoFeedback, ComensalFeedback, PlatosSeleccionados,
    RecomendacionComensal, SeleccionPlatosResponse, EstadoMesa, MesaUpdate,
)
from app.services.ml_client import run_inference
from app.services.dynamo_client import save_registro, update_feedback_by_key, increment_visitas_cliente
from app.services.mesa_service import get_mesa, update_mesa

logger = logging.getLogger("bistrotech.pedido_service")

TABLE_PEDIDOS = os.getenv("DYNAMODB_TABLE_PEDIDOS", "bistrotech-pedidos")
ENDPOINT_URL  = os.getenv("DYNAMODB_ENDPOINT_URL")
CLIENTES_POR_MESA = os.getenv("CLIENTES_POR_MESA", "bistrotech-registros") # Mesa que tiene todos los clientes y la mesa que eligieron

_PEDIDOS_LOCAL: dict[str, dict] = {}
_PREFERENCIAS_LOCAL: dict[str, dict] = {}


def _to_dynamo_value(value):
    if isinstance(value, float):
        return Decimal(str(value))
    return value


def _get_table(table_name : str = TABLE_PEDIDOS):
    kwargs = {"region_name": os.getenv("AWS_REGION", "us-east-1")}
    if ENDPOINT_URL:
        kwargs["endpoint_url"] = ENDPOINT_URL
    return boto3.resource("dynamodb", **kwargs).Table(table_name)


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


def _platos_from_item(item: dict) -> Optional[PlatosSeleccionados]:
    if "id_principal" not in item or "id_bebida" not in item:
        return None
    return PlatosSeleccionados(
        id_entrada=item.get("id_entrada"),
        id_principal=int(item["id_principal"]),
        id_postre=item.get("id_postre"),
        id_bebida=int(item["id_bebida"]),
    )


def _comensal_item(id_mesa: int, codigo_pedido: str, comensal: Comensal, ts: str) -> dict:
    data = comensal.model_dump(mode="json")
    item = {
        "id_mesa": id_mesa,
        "persona_ts": f"{comensal.id_persona_en_mesa}#{ts}",
        "codigo_pedido": codigo_pedido,
        "estado": EstadoPedido.pendiente.value,
        "fecha_hora": ts,
        **data,
    }
    return {k: v for k, v in item.items() if v is not None}


def _to_comensal_pedido_response(item: dict) -> ComensalPedidoResponse:
    estado = item.get("estado", EstadoPedido.pendiente.value)
    id_pedido = item.get("id_pedido")
    if id_pedido:
        pedido = _get_pedido_item(id_pedido)
        if pedido:
            estado = pedido.get("estado", estado)

    return ComensalPedidoResponse(
        codigo_pedido=item["codigo_pedido"],
        id_mesa=int(item["id_mesa"]),
        estado=estado,
        fecha_hora=item["fecha_hora"],
        comensal=Comensal.model_validate(item),
        id_pedido=id_pedido,
        platos_seleccionados=_platos_from_item(item),
    )


def _get_pedido_item(id_pedido: str) -> Optional[dict]:
    try:
        resp = _get_table().get_item(Key={"id_pedido": id_pedido})
        return resp.get("Item") or _PEDIDOS_LOCAL.get(id_pedido)
    except (BotoCoreError, ClientError) as exc:
        logger.warning("Error consultando pedido %s: %s", id_pedido, exc)
        return _PEDIDOS_LOCAL.get(id_pedido)


def registrar_preferencia(req: ComensalPedidoCreate) -> ComensalPedidoResponse:
    ts = datetime.now(timezone.utc).isoformat()
    codigo_pedido = uuid.uuid4().hex[:8].upper()
    comensal_data = req.comensal.model_dump(mode="json", exclude_none=True)
    comensal_data["id_persona_en_mesa"] = _next_id_persona_en_mesa(req.id_mesa)
    comensal = Comensal.model_validate(comensal_data)
    item = _comensal_item(req.id_mesa, codigo_pedido, comensal, ts)

    try:
        _get_table(CLIENTES_POR_MESA).put_item(Item=item)
        logger.info(
            "Preferencia guardada: codigo_pedido=%s id_mesa=%s",
            codigo_pedido,
            req.id_mesa,
        )
    except (BotoCoreError, ClientError) as exc:
        logger.warning("No se pudo guardar preferencia en DynamoDB: %s", exc)
        _PREFERENCIAS_LOCAL[codigo_pedido] = item

    _marcar_mesa_ocupada_si_llena(req.id_mesa)

    return _to_comensal_pedido_response(item)


def _get_preferencia_item(codigo_pedido: str) -> Optional[dict]:
    try:
        table = _get_table(CLIENTES_POR_MESA)
        resp = table.scan(FilterExpression=Attr("codigo_pedido").eq(codigo_pedido))
        items = resp.get("Items", [])
        return items[0] if items else _PREFERENCIAS_LOCAL.get(codigo_pedido)
    except (BotoCoreError, ClientError) as exc:
        logger.warning("Error consultando preferencia %s: %s", codigo_pedido, exc)
        return _PREFERENCIAS_LOCAL.get(codigo_pedido)


def _preferencias_local_por_mesa(id_mesa: int, solo_pendientes: bool = False) -> list[dict]:
    return [
        item
        for item in _PREFERENCIAS_LOCAL.values()
        if int(item["id_mesa"]) == id_mesa
        and (
            not solo_pendientes
            or item.get("estado") == EstadoPedido.pendiente.value
        )
    ]


def _all_preferencia_items_for_mesa(id_mesa: int) -> list[dict]:
    try:
        resp = _get_table(CLIENTES_POR_MESA).query(
            KeyConditionExpression=Key("id_mesa").eq(id_mesa),
        )
        items = [
            item
            for item in resp.get("Items", [])
            if "codigo_pedido" in item
        ]
    except (BotoCoreError, ClientError) as exc:
        logger.warning("Error listando preferencias id_mesa=%s: %s", id_mesa, exc)
        items = []

    codigos = {item["codigo_pedido"] for item in items}
    items.extend(
        item
        for item in _preferencias_local_por_mesa(id_mesa)
        if item["codigo_pedido"] not in codigos
    )
    return items


def _next_id_persona_en_mesa(id_mesa: int) -> int:
    ids = [
        int(item["id_persona_en_mesa"])
        for item in _all_preferencia_items_for_mesa(id_mesa)
        if item.get("id_persona_en_mesa") is not None
    ]
    return max(ids, default=0) + 1


def _cantidad_comensales_actuales(id_mesa: int) -> int:
    return sum(
        1
        for item in _all_preferencia_items_for_mesa(id_mesa)
        if item.get("estado") != EstadoPedido.cerrado.value
    )


def _marcar_mesa_ocupada_si_llena(id_mesa: int) -> None:
    mesa = get_mesa(id_mesa)
    if not mesa:
        return

    if _cantidad_comensales_actuales(id_mesa) < mesa.capacidad:
        return

    if mesa.estado == EstadoMesa.ocupada:
        return

    update_mesa(id_mesa, MesaUpdate(estado=EstadoMesa.ocupada))
    logger.info("Mesa marcada ocupada: id_mesa=%s", id_mesa)


def _list_preferencia_items(id_mesa: int) -> list[dict]:
    items = [
        item
        for item in _all_preferencia_items_for_mesa(id_mesa)
        if item.get("estado") == EstadoPedido.pendiente.value
    ]

    return sorted(
        items,
        key=lambda item: (int(item.get("orden_de_pedido", 0)), item.get("fecha_hora", "")),
    )


def _mark_preferencias_en_pedido(items: list[dict], id_pedido: str) -> None:
    for item in items:
        codigo_pedido = item.get("codigo_pedido")
        if codigo_pedido in _PREFERENCIAS_LOCAL:
            _PREFERENCIAS_LOCAL[codigo_pedido]["estado"] = EstadoPedido.activo.value
            _PREFERENCIAS_LOCAL[codigo_pedido]["id_pedido"] = id_pedido

        try:
            _get_table(CLIENTES_POR_MESA).update_item(
                Key={"id_mesa": int(item["id_mesa"]), "persona_ts": item["persona_ts"]},
                UpdateExpression="SET estado = :estado, id_pedido = :id_pedido",
                ExpressionAttributeValues={
                    ":estado": EstadoPedido.activo.value,
                    ":id_pedido": id_pedido,
                },
            )
        except (BotoCoreError, ClientError) as exc:
            logger.warning("No se pudo vincular preferencia %s: %s", codigo_pedido, exc)


def get_estado_preferencia(codigo_pedido: str) -> Optional[ComensalPedidoResponse]:
    item = _get_preferencia_item(codigo_pedido)
    return _to_comensal_pedido_response(item) if item else None


def get_platos_recomendados(codigo_pedido: str) -> Optional[RecomendacionComensal]:
    preferencia = _get_preferencia_item(codigo_pedido)
    if not preferencia:
        return None

    id_pedido = preferencia.get("id_pedido")
    if not id_pedido:
        raise ValueError("Todavía no se generaron recomendaciones para este código")

    pedido = _get_pedido_item(id_pedido)
    if not pedido:
        return None

    id_persona = int(preferencia["id_persona_en_mesa"])
    recomendaciones = json.loads(pedido["recomendaciones_por_comensal"])
    for recomendacion in recomendaciones:
        if int(recomendacion["id_persona_en_mesa"]) == id_persona:
            return RecomendacionComensal.model_validate(recomendacion)

    return None


def create_pedido(id_mesa: int, req: PedidoMesaCreate | PedidoCreate) -> PedidoResponse:
    ts = datetime.now(timezone.utc).isoformat()
    comensales = (
        req.comensales
        if isinstance(req, PedidoCreate)
        else [Comensal.model_validate(item) for item in _list_preferencia_items(id_mesa)]
    )
    if not comensales:
        raise ValueError(f"No hay preferencias pendientes para la mesa {id_mesa}")

    predict_req = PredictRequest(
        id_mesa=id_mesa,
        comensales=comensales,
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
        _PEDIDOS_LOCAL[id_pedido] = item

    if not isinstance(req, PedidoCreate):
        _mark_preferencias_en_pedido(_list_preferencia_items(id_mesa), id_pedido)

    return _to_response(item)


def get_pedido(id_pedido: str) -> Optional[PedidoResponse]:
    item = _get_pedido_item(id_pedido)
    return _to_response(item) if item else None


def actualizar_estado_pedido(
    id_pedido: str,
    req: PedidoEstadoUpdate,
) -> Optional[PedidoResponse]:
    item = _get_pedido_item(id_pedido)
    if not item:
        return None

    item["estado"] = req.estado.value
    if id_pedido in _PEDIDOS_LOCAL:
        _PEDIDOS_LOCAL[id_pedido]["estado"] = req.estado.value

    try:
        _get_table().update_item(
            Key={"id_pedido": id_pedido},
            UpdateExpression="SET estado = :estado",
            ExpressionAttributeValues={":estado": req.estado.value},
        )
    except (BotoCoreError, ClientError) as exc:
        logger.warning("No se pudo actualizar estado pedido %s: %s", id_pedido, exc)

    logger.info("Estado actualizado: id_pedido=%s estado=%s", id_pedido, req.estado.value)
    return _to_response(item)


def seleccionar_platos(
    codigo_pedido: str,
    req: PlatosSeleccionados,
) -> Optional[SeleccionPlatosResponse]:
    preferencia = _get_preferencia_item(codigo_pedido)
    if not preferencia:
        return None

    id_pedido = preferencia.get("id_pedido")
    if not id_pedido:
        raise ValueError("Todavía no se generaron recomendaciones para este código")

    pedido = _get_pedido_item(id_pedido)
    if not pedido:
        return None

    seleccion = req.model_dump(exclude_none=True)
    preferencia.update(seleccion)
    preferencia["estado"] = EstadoPedido.confirmado.value
    pedido["estado"] = EstadoPedido.confirmado.value

    if codigo_pedido in _PREFERENCIAS_LOCAL:
        _PREFERENCIAS_LOCAL[codigo_pedido].update(preferencia)
    if id_pedido in _PEDIDOS_LOCAL:
        _PEDIDOS_LOCAL[id_pedido]["estado"] = EstadoPedido.confirmado.value

    try:
        update_parts = ["estado = :estado"]
        values = {":estado": EstadoPedido.confirmado.value}
        for field, value in seleccion.items():
            update_parts.append(f"{field} = :{field}")
            values[f":{field}"] = value

        _get_table(CLIENTES_POR_MESA).update_item(
            Key={
                "id_mesa": int(preferencia["id_mesa"]),
                "persona_ts": preferencia["persona_ts"],
            },
            UpdateExpression="SET " + ", ".join(update_parts),
            ExpressionAttributeValues=values,
        )
    except (BotoCoreError, ClientError) as exc:
        logger.warning("No se pudo guardar selección %s: %s", codigo_pedido, exc)

    try:
        _get_table(CLIENTES_POR_MESA).update_item(
            Key={
                "id_mesa": int(preferencia["id_mesa"]),
                "persona_ts": (
                    f"{preferencia['id_persona_en_mesa']}#{pedido['fecha_hora']}"
                ),
            },
            UpdateExpression="SET "
            + ", ".join(f"{field} = :{field}" for field in seleccion),
            ExpressionAttributeValues={f":{field}": value for field, value in seleccion.items()},
        )
    except (BotoCoreError, ClientError) as exc:
        logger.warning("No se pudo guardar selección en registro ML %s: %s", codigo_pedido, exc)

    actualizar_estado_pedido(id_pedido, PedidoEstadoUpdate(estado=EstadoPedido.confirmado))

    logger.info("Platos seleccionados: codigo_pedido=%s id_pedido=%s", codigo_pedido, id_pedido)
    return SeleccionPlatosResponse(
        ok=True,
        codigo_pedido=codigo_pedido,
        id_pedido=id_pedido,
        estado=EstadoPedido.confirmado,
        platos_seleccionados=req,
    )


def list_pedidos(id_mesa: int) -> list[Comensal]:
    return [Comensal.model_validate(item) for item in _list_preferencia_items(id_mesa)]


def finalizar_comida_por_codigo(
    codigo_pedido: str,
    req: ComensalCodigoFeedback,
) -> Optional[PedidoFeedbackResponse]:
    preferencia = _get_preferencia_item(codigo_pedido)
    if not preferencia:
        return None

    id_pedido = preferencia.get("id_pedido")
    if not id_pedido:
        raise ValueError("Todavía no se generaron recomendaciones para este código")

    pedido = _get_pedido_item(id_pedido)
    if not pedido:
        return None

    feedback_data = req.model_dump(mode="json", exclude_none=True)
    comensal_feedback = ComensalFeedback(
        id_persona_en_mesa=int(preferencia["id_persona_en_mesa"]),
        **feedback_data,
    )
    persona_ts = f"{preferencia['id_persona_en_mesa']}#{pedido['fecha_hora']}"
    id_cliente = update_feedback_by_key(
        int(preferencia["id_mesa"]),
        persona_ts,
        comensal_feedback,
    )
    actualizados = 0 if id_cliente == -1 else 1
    if id_cliente not in (-1, None):
        increment_visitas_cliente(id_cliente)

    preferencia.update(feedback_data)
    preferencia["estado"] = EstadoPedido.cerrado.value
    if codigo_pedido in _PREFERENCIAS_LOCAL:
        _PREFERENCIAS_LOCAL[codigo_pedido].update(preferencia)

    try:
        update_parts = ["estado = :estado"]
        values = {":estado": EstadoPedido.cerrado.value}
        for field, value in feedback_data.items():
            update_parts.append(f"{field} = :{field}")
            values[f":{field}"] = _to_dynamo_value(value)

        _get_table(CLIENTES_POR_MESA).update_item(
            Key={
                "id_mesa": int(preferencia["id_mesa"]),
                "persona_ts": preferencia["persona_ts"],
            },
            UpdateExpression="SET " + ", ".join(update_parts),
            ExpressionAttributeValues=values,
        )
        if actualizados == 0:
            actualizados = 1
    except (BotoCoreError, ClientError, TypeError) as exc:
        logger.warning("No se pudo guardar feedback para %s: %s", codigo_pedido, exc)
        if codigo_pedido in _PREFERENCIAS_LOCAL:
            actualizados = 1

    actualizar_estado_pedido(id_pedido, PedidoEstadoUpdate(estado=EstadoPedido.cerrado))

    logger.info("Comida finalizada: codigo_pedido=%s id_pedido=%s", codigo_pedido, id_pedido)
    return PedidoFeedbackResponse(
        ok=actualizados > 0,
        id_pedido=id_pedido,
        registros_actualizados=actualizados,
    )


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
