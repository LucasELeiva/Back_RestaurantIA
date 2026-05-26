from fastapi import APIRouter, HTTPException
from app.models.schemas import PedidoResponse, PedidoFeedbackRequest, PedidoFeedbackResponse
from app.services.pedido_service import get_pedido, submit_feedback
import logging

router = APIRouter()
logger = logging.getLogger("bistrotech.router.pedidos")


@router.get("/{id_pedido}", response_model=PedidoResponse)
def obtener_pedido(id_pedido: str) -> PedidoResponse:
    pedido = get_pedido(id_pedido)
    if not pedido:
        raise HTTPException(status_code=404, detail=f"Pedido {id_pedido} no encontrado")
    return pedido


@router.post("/{id_pedido}/feedback", response_model=PedidoFeedbackResponse)
def feedback_pedido(id_pedido: str, req: PedidoFeedbackRequest) -> PedidoFeedbackResponse:
    """
    Recibe el feedback post-servicio del POS para todos los comensales del pedido.
    Completa los registros en DynamoDB para el reentrenamiento del modelo.
    """
    result = submit_feedback(id_pedido, req)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Pedido {id_pedido} no encontrado")
    return result
