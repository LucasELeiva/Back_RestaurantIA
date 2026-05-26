import os
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from boto3.dynamodb.conditions import Attr

from app.models.schemas import Reserva, ReservaCreate, ReservaUpdate, EstadoReserva

logger = logging.getLogger("bistrotech.reserva_service")

TABLE_RESERVAS = os.getenv("DYNAMODB_TABLE_RESERVAS", "bistrotech-reservas")
ENDPOINT_URL   = os.getenv("DYNAMODB_ENDPOINT_URL")


def _get_table():
    kwargs = {"region_name": os.getenv("AWS_REGION", "us-east-1")}
    if ENDPOINT_URL:
        kwargs["endpoint_url"] = ENDPOINT_URL
    return boto3.resource("dynamodb", **kwargs).Table(TABLE_RESERVAS)


def _to_reserva(item: dict) -> Reserva:
    return Reserva(
        id_reserva=item["id_reserva"],
        id_mesa=int(item["id_mesa"]),
        nombre_cliente=item["nombre_cliente"],
        id_cliente=int(item["id_cliente"]) if item.get("id_cliente") is not None else None,
        fecha_hora=item["fecha_hora"],
        cantidad_personas=int(item["cantidad_personas"]),
        motivo_visita=item.get("motivo_visita"),
        estado=item["estado"],
        notas=item.get("notas"),
        email=item.get("email"),
        telefono=item.get("telefono"),
        created_at=item["created_at"],
    )


def create_reserva(req: ReservaCreate) -> Reserva:
    table = _get_table()
    now = datetime.now(timezone.utc).isoformat()
    item: dict = {
        "id_reserva":        str(uuid.uuid4()),
        "id_mesa":           req.id_mesa,
        "nombre_cliente":    req.nombre_cliente,
        "fecha_hora":        req.fecha_hora,
        "cantidad_personas": req.cantidad_personas,
        "estado":            EstadoReserva.confirmada.value,
        "created_at":        now,
    }
    if req.id_cliente is not None:
        item["id_cliente"] = req.id_cliente
    if req.motivo_visita is not None:
        item["motivo_visita"] = req.motivo_visita.value
    if req.notas is not None:
        item["notas"] = req.notas
    if req.email is not None:
        item["email"] = req.email
    if req.telefono is not None:
        item["telefono"] = req.telefono

    table.put_item(Item=item)
    logger.info("Reserva creada: id_reserva=%s id_mesa=%s", item["id_reserva"], req.id_mesa)
    return _to_reserva(item)


def get_reserva(id_reserva: str) -> Optional[Reserva]:
    try:
        resp = _get_table().get_item(Key={"id_reserva": id_reserva})
        item = resp.get("Item")
        return _to_reserva(item) if item else None
    except (BotoCoreError, ClientError) as exc:
        logger.warning("Error consultando reserva %s: %s", id_reserva, exc)
        return None


def list_reservas(id_mesa: Optional[int] = None) -> list[Reserva]:
    try:
        kwargs = {}
        if id_mesa is not None:
            kwargs["FilterExpression"] = Attr("id_mesa").eq(id_mesa)
        resp = _get_table().scan(**kwargs)
        reservas = [_to_reserva(i) for i in resp.get("Items", [])]
        return sorted(reservas, key=lambda r: r.fecha_hora)
    except (BotoCoreError, ClientError) as exc:
        logger.warning("Error listando reservas: %s", exc)
        return []


def update_reserva(id_reserva: str, req: ReservaUpdate) -> Optional[Reserva]:
    updates = {k: v for k, v in req.model_dump(exclude_none=True).items()}
    if not updates:
        return get_reserva(id_reserva)

    for field in ("motivo_visita", "estado"):
        if field in updates and hasattr(updates[field], "value"):
            updates[field] = updates[field].value

    expr_parts, expr_names, expr_values = [], {}, {}
    for i, (k, v) in enumerate(updates.items()):
        name_ph, val_ph = f"#f{i}", f":v{i}"
        expr_parts.append(f"{name_ph} = {val_ph}")
        expr_names[name_ph] = k
        expr_values[val_ph] = v

    try:
        resp = _get_table().update_item(
            Key={"id_reserva": id_reserva},
            UpdateExpression="SET " + ", ".join(expr_parts),
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
            ConditionExpression=Attr("id_reserva").exists(),
            ReturnValues="ALL_NEW",
        )
        return _to_reserva(resp["Attributes"])
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None
        raise


def cancel_reserva(id_reserva: str) -> bool:
    result = update_reserva(id_reserva, ReservaUpdate(estado=EstadoReserva.cancelada))
    return result is not None
