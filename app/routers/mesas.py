from fastapi import APIRouter, HTTPException
from app.models.schemas import Mesa, MesaCreate, MesaUpdate
from app.services.mesa_service import create_mesa, get_mesa, list_mesas, update_mesa, delete_mesa

from botocore.exceptions import ClientError
import logging

router = APIRouter()
logger = logging.getLogger("bistrotech.router.mesas")


@router.post("", response_model=Mesa, status_code=201)
def crear_mesa(req: MesaCreate) -> Mesa:
    try:
        return create_mesa(req)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise HTTPException(status_code=409, detail=f"Ya existe una mesa con id_mesa={req.id_mesa}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("", response_model=list[Mesa])
def listar_mesas(solo_activas: bool = True) -> list[Mesa]:
    return list_mesas(solo_activas=solo_activas)


@router.get("/{id_mesa}", response_model=Mesa)
def obtener_mesa(id_mesa: int) -> Mesa:
    mesa = get_mesa(id_mesa)
    if not mesa:
        raise HTTPException(status_code=404, detail=f"Mesa {id_mesa} no encontrada")
    return mesa


@router.patch("/{id_mesa}", response_model=Mesa)
def actualizar_mesa(id_mesa: int, req: MesaUpdate) -> Mesa:
    mesa = update_mesa(id_mesa, req)
    if not mesa:
        raise HTTPException(status_code=404, detail=f"Mesa {id_mesa} no encontrada")
    return mesa


@router.delete("/{id_mesa}", status_code=204)
def eliminar_mesa(id_mesa: int) -> None:
    if not delete_mesa(id_mesa):
        raise HTTPException(status_code=404, detail=f"Mesa {id_mesa} no encontrada")




