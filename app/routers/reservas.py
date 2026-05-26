from fastapi import APIRouter, HTTPException
from typing import Optional
from app.models.schemas import Reserva, ReservaCreate, ReservaUpdate
from app.services.reserva_service import (
    create_reserva, get_reserva, list_reservas, update_reserva, cancel_reserva,
)
import logging

router = APIRouter()
logger = logging.getLogger("bistrotech.router.reservas")


@router.post("", response_model=Reserva, status_code=201)
def crear_reserva(req: ReservaCreate) -> Reserva:
    try:
        return create_reserva(req)
    except Exception as exc:
        logger.exception("Error creando reserva")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("", response_model=list[Reserva])
def listar_reservas(id_mesa: Optional[int] = None) -> list[Reserva]:
    return list_reservas(id_mesa=id_mesa)


@router.get("/{id_reserva}", response_model=Reserva)
def obtener_reserva(id_reserva: str) -> Reserva:
    reserva = get_reserva(id_reserva)
    if not reserva:
        raise HTTPException(status_code=404, detail=f"Reserva {id_reserva} no encontrada")
    return reserva


@router.patch("/{id_reserva}", response_model=Reserva)
def actualizar_reserva(id_reserva: str, req: ReservaUpdate) -> Reserva:
    reserva = update_reserva(id_reserva, req)
    if not reserva:
        raise HTTPException(status_code=404, detail=f"Reserva {id_reserva} no encontrada")
    return reserva


@router.delete("/{id_reserva}", status_code=204)
def cancelar_reserva(id_reserva: str) -> None:
    if not cancel_reserva(id_reserva):
        raise HTTPException(status_code=404, detail=f"Reserva {id_reserva} no encontrada")
