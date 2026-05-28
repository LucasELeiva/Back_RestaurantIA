from fastapi import APIRouter, HTTPException
from app.models.schemas import (
    Comensal,
    ComensalCodigoFeedback,
    ComensalPedidoCreate,
    ComensalPedidoResponse,
    PedidoEstadoUpdate,
    PedidoFeedbackRequest,
    PedidoFeedbackResponse,
    PedidoMesaCreate,
    PedidoResponse,
    PlatosSeleccionados,
    RecomendacionComensal,
    SeleccionPlatosResponse,
)
from app.services.pedido_service import (
    actualizar_estado_pedido,
    create_pedido,
    finalizar_comida_por_codigo,
    get_estado_preferencia,
    get_pedido,
    get_platos_recomendados,
    list_pedidos,
    registrar_preferencia,
    seleccionar_platos,
    submit_feedback,
)
import logging

router = APIRouter()
logger = logging.getLogger("bistrotech.router.pedidos")


@router.post("/preferencias", response_model=ComensalPedidoResponse, status_code=201)
def crear_preferencia(req: ComensalPedidoCreate) -> ComensalPedidoResponse:
    """
    Registra el perfil de gustos de un comensal y devuelve un código consultable.
    """
    try:
        return registrar_preferencia(req)
    except Exception as exc:
        logger.exception("Error creando preferencia para mesa %s", req.id_mesa)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/preferencias/{codigo_pedido}", response_model=ComensalPedidoResponse)
def obtener_estado_preferencia(codigo_pedido: str) -> ComensalPedidoResponse:
    preferencia = get_estado_preferencia(codigo_pedido)
    if not preferencia:
        raise HTTPException(status_code=404, detail=f"Código {codigo_pedido} no encontrado")
    return preferencia


@router.get(
    "/preferencias/{codigo_pedido}/platos",
    response_model=RecomendacionComensal,
)
def listar_platos_recomendados(codigo_pedido: str) -> RecomendacionComensal:
    try:
        recomendaciones = get_platos_recomendados(codigo_pedido)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not recomendaciones:
        raise HTTPException(status_code=404, detail=f"Código {codigo_pedido} no encontrado")
    return recomendaciones


@router.post(
    "/preferencias/{codigo_pedido}/platos",
    response_model=SeleccionPlatosResponse,
)
def elegir_platos(codigo_pedido: str, req: PlatosSeleccionados) -> SeleccionPlatosResponse:
    try:
        seleccion = seleccionar_platos(codigo_pedido, req)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not seleccion:
        raise HTTPException(status_code=404, detail=f"Código {codigo_pedido} no encontrado")
    return seleccion


@router.post(
    "/preferencias/{codigo_pedido}/feedback",
    response_model=PedidoFeedbackResponse,
)
def finalizar_comida(codigo_pedido: str, req: ComensalCodigoFeedback) -> PedidoFeedbackResponse:
    try:
        result = finalizar_comida_por_codigo(codigo_pedido, req)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not result:
        raise HTTPException(status_code=404, detail=f"Código {codigo_pedido} no encontrado")
    return result


@router.post("/pedidos/{id_mesa}", response_model=PedidoResponse, status_code=201)
def crear_pedido(id_mesa: int, req: PedidoMesaCreate) -> PedidoResponse:
    """
    Crea un pedido para la mesa usando las preferencias pendientes de sus comensales.
    """
    try:
        return create_pedido(id_mesa, req)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.exception("Error creando pedido para mesa %s", id_mesa)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{id_mesa}/pedidos", response_model=list[Comensal])
def listar_pedidos_mesa(id_mesa: int) -> list[Comensal]:
    return list_pedidos(id_mesa)


@router.patch("/{id_pedido}/estado", response_model=PedidoResponse)
def cambiar_estado_pedido(id_pedido: str, req: PedidoEstadoUpdate) -> PedidoResponse:
    pedido = actualizar_estado_pedido(id_pedido, req)
    if not pedido:
        raise HTTPException(status_code=404, detail=f"Pedido {id_pedido} no encontrado")
    return pedido


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
