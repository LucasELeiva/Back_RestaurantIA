import os
import logging
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from boto3.dynamodb.conditions import Attr, Key

from app.models.schemas import Mesa, MesaCreate, MesaUpdate, EstadoMesa

logger = logging.getLogger("bistrotech.mesa_service")

TABLE_MESAS   = os.getenv("DYNAMODB_TABLE_MESAS", "bistrotech-mesas")
TABLE_REGISTROS = os.getenv("DYNAMODB_TABLE_REGISTROS", "bistrotech-registros")
ENDPOINT_URL  = os.getenv("DYNAMODB_ENDPOINT_URL")


def _get_table(table_name: str = TABLE_MESAS):
    kwargs = {"region_name": os.getenv("AWS_REGION", "us-east-1")}
    if ENDPOINT_URL:
        kwargs["endpoint_url"] = ENDPOINT_URL
    return boto3.resource("dynamodb", **kwargs).Table(table_name)


def _cantidad_personas_mesa(id_mesa: int) -> int:
    try:
        resp = _get_table(TABLE_REGISTROS).query(
            KeyConditionExpression=Key("id_mesa").eq(id_mesa),
            FilterExpression=Attr("codigo_pedido").exists() & Attr("estado").ne("cerrado"),
        )
        return len(resp.get("Items", []))
    except (BotoCoreError, ClientError) as exc:
        logger.warning("Error contando personas en mesa %s: %s", id_mesa, exc)
        return 0


def _to_mesa(item: dict, incluir_cantidad_personas: bool = False) -> Mesa:
    id_mesa = int(item["id_mesa"])
    return Mesa(
        id_mesa=id_mesa,
        capacidad=int(item["capacidad"]),
        ubicacion=item["ubicacion"],
        estado=item["estado"],
        activa=item["activa"],
        cantidad_personas=(
            _cantidad_personas_mesa(id_mesa)
            if incluir_cantidad_personas
            else int(item.get("cantidad_personas", 0))
        ),
    )


def create_mesa(req: MesaCreate) -> Mesa:
    table = _get_table()
    item = {
        "id_mesa":    req.id_mesa,
        "capacidad":  req.capacidad,
        "ubicacion":  req.ubicacion.value,
        "estado":     EstadoMesa.libre.value,
        "activa":     True,
    }
    table.put_item(Item=item, ConditionExpression=Attr("id_mesa").not_exists())
    logger.info("Mesa creada: id_mesa=%s", req.id_mesa)
    return _to_mesa(item)


def get_mesa(id_mesa: int) -> Optional[Mesa]:
    try:
        resp = _get_table().get_item(Key={"id_mesa": id_mesa})
        item = resp.get("Item")
        return _to_mesa(item) if item else None
    except (BotoCoreError, ClientError) as exc:
        logger.warning("Error consultando mesa %s: %s", id_mesa, exc)
        return None


def list_mesas(solo_activas: bool = True) -> list[Mesa]:
    try:
        kwargs = {}
        if solo_activas:
            kwargs["FilterExpression"] = Attr("activa").eq(True)
        resp = _get_table().scan(**kwargs)
        return [
            _to_mesa(i, incluir_cantidad_personas=True)
            for i in resp.get("Items", [])
        ]   
    except (BotoCoreError, ClientError) as exc:
        logger.warning("Error listando mesas: %s", exc)
        return []


def update_mesa(id_mesa: int, req: MesaUpdate) -> Optional[Mesa]:
    updates = {k: v for k, v in req.model_dump(exclude_none=True).items()}
    if not updates:
        return get_mesa(id_mesa)

    # Convertir enums a sus valores string
    for field in ("ubicacion", "estado"):
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
            Key={"id_mesa": id_mesa},
            UpdateExpression="SET " + ", ".join(expr_parts),
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
            ConditionExpression=Attr("id_mesa").exists(),
            ReturnValues="ALL_NEW",
        )
        return _to_mesa(resp["Attributes"])
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None
        raise


def delete_mesa(id_mesa: int) -> bool:
    """Soft delete — marca activa=False."""
    result = update_mesa(id_mesa, MesaUpdate(activa=False))
    return result is not None
