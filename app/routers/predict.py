from fastapi import APIRouter, HTTPException
from app.models.schemas import PredictRequest, PredictResponse, FeedbackRequest, FeedbackResponse
from app.services.ml_client import run_inference
from app.services.dynamo_client import save_registro, update_feedback
import logging

router = APIRouter()
logger = logging.getLogger("bistrotech.router")


@router.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest) -> PredictResponse:
    """
    Recibe el contexto de la mesa + comensales y devuelve:
    - Ranking de mozos ordenado por propina_rate esperado (Modelo A)
    - Top-3 platos por curso para cada comensal (Modelos B ×4)
    """
    try:
        resp = run_inference(req)
        save_registro(req, resp)
        return resp
    except Exception as exc:
        logger.exception("Error inesperado en inferencia")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/feedback", response_model=FeedbackResponse)
def feedback(req: FeedbackRequest) -> FeedbackResponse:
    """
    Recibe el feedback post-servicio del POS y completa el registro en DynamoDB.
    Campos obligatorios: id_mozo, id_principal, id_bebida.
    Campos opcionales: likes, propina, proporciones dejadas, platos nullable.
    """
    try:
        actualizados = update_feedback(req)
        if actualizados == 0:
            raise HTTPException(
                status_code=404,
                detail=f"No se encontró registro para id_mesa={req.id_mesa} id_persona_en_mesa={req.id_persona_en_mesa}",
            )
        return FeedbackResponse(
            ok=True,
            id_mesa=req.id_mesa,
            id_persona_en_mesa=req.id_persona_en_mesa,
            registros_actualizados=actualizados,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error inesperado en feedback")
        raise HTTPException(status_code=500, detail=str(exc))
