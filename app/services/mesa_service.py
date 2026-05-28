import os
import logging
from collections import defaultdict
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from boto3.dynamodb.conditions import Attr

from app.models.schemas import Mesa, MesaCreate, MesaUpdate, EstadoMesa

logger = logging.getLogger("bistrotech.mesa_service")

TABLE_MESAS   = os.getenv("DYNAMODB_TABLE_MESAS", "bistrotech-mesas")
TABLE_REGISTROS = os.getenv("DYNAMODB_TABLE_REGISTROS", "bistrotech-registros")
ENDPOINT_URL  = os.getenv("DYNAMODB_ENDPOINT_URL")

_DYNAMODB_RESOURCE = None


def _get_resource():
    global _DYNAMODB_RESOURCE
    if _DYNAMODB_RESOURCE is None:
        kwargs = {"region_name": os.getenv("AWS_REGION", "us-east-1")}
        if ENDPOINT_URL:
            kwargs["endpoint_url"] = ENDPOINT_URL
        _DYNAMODB_RESOURCE = boto3.resource("dynamodb", **kwargs)
    return _DYNAMODB_RESOURCE


def _get_table(table_name: str = TABLE_MESAS):
    return _get_resource().Table(table_name)


def _contar_personas_por_mesa() -> dict[int, int]:
    """
    Una sola pasada por registros (scan paginado) en lugar de N queries por mesa.
    """
    counts: dict[int, int] = defaultdict(int)
    filtro = Attr("codigo_pedido").exists() & Attr("estado").ne("cerrado")
    try:
        table = _get_table(TABLE_REGISTROS)
        resp = table.scan(
            FilterExpression=filtro,
            ProjectionExpression="id_mesa",
        )
        for item in resp.get("Items", []):
            counts[int(item["id_mesa"])] += 1
        while "LastEvaluatedKey" in resp:
            resp = table.scan(
                FilterExpression=filtro,
                ProjectionExpression="id_mesa",
                ExclusiveStartKey=resp["LastEvaluatedKey"],
            )
            for item in resp.get("Items", []):
                counts[int(item["id_mesa"])] += 1
    except (BotoCoreError, ClientError) as exc:
        logger.warning("Error contando personas por mesa: %s", exc)
    return dict(counts)


def _to_mesa(item: dict, cantidad_personas: int = 0) -> Mesa:
    return Mesa(
        id_mesa=int(item["id_mesa"]),
        capacidad=int(item["capacidad"]),
        ubicacion=item["ubicacion"],
        estado=item["estado"],
        activa=item["activa"],
        cantidad_personas=cantidad_personas,
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
        personas_por_mesa = _contar_personas_por_mesa()
        return [
            _to_mesa(i, personas_por_mesa.get(int(i["id_mesa"]), 0))
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
