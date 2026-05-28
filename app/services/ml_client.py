"""
Servicio de inferencia — llama al modelo en AWS SageMaker.

Configuración vía variables de entorno:
  ML_BACKEND          → "sagemaker" | "local"  (default: local)
  SAGEMAKER_ENDPOINT  → nombre del endpoint en SageMaker
  AWS_REGION          → región AWS (default: us-east-1)

Para desarrollo local corre el fallback determinístico sin credenciales AWS.
"""

import os
import time
import json
import logging
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.models.schemas import PredictRequest, PredictResponse
from app.services.dynamo_client import get_cliente_historico, get_segmento_referencia

logger = logging.getLogger("bistrotech.ml_client")

# ── Carta Bellavista — mapeo id_plato → {nombre, descripcion, precio} ──────
PLATOS: dict[int, dict] = {
    # Entradas (1-8): aperitivos + sopas + ensalada
    1:  {"nombre": "Ostras al natural con mignonette de champagne",
         "descripcion": "Media docena de ostras frescas de Ushuaia, salsa mignonette con echalotte y vinagre de champagne, limón Meyer",
         "precio": 18500},
    2:  {"nombre": "Foie gras mi-cuit",
         "descripcion": "Torchon de foie gras con compota de higos, brioche tostado y fleur de sel",
         "precio": 22000},
    3:  {"nombre": "Carpaccio de lomo Aberdeen Angus",
         "descripcion": "Finas láminas de lomo premium, aceite de trufa, rúcula silvestre y parmesano reggiano 24 meses",
         "precio": 16800},
    4:  {"nombre": "Burrata con tomates heredados",
         "descripcion": "Burrata cremosa de producción propia, tomates heirloom de estación, pesto de albahaca y aceite de oliva extra virgen mendocino",
         "precio": 14200},
    5:  {"nombre": "Bisque de langostinos patagónicos",
         "descripcion": "Bisque concentrada con langostinos salteados, crema de coco, aceite de curry y ciboulette",
         "precio": 15400},
    6:  {"nombre": "Velouté de hongos silvestres",
         "descripcion": "Mezcla de hongos porcini, portobello y shiitake, trufa rallada y aceite de avellana tostada",
         "precio": 12600},
    7:  {"nombre": "Consommé doble de res",
         "descripcion": "Consommé clarificado, dumplings de tuétano, brunoise de verduras y hierbas frescas",
         "precio": 11800},
    8:  {"nombre": "Ensalada de rúcula, pera y gorgonzola",
         "descripcion": "Hojas de rúcula fresca, pera Williams caramelizada, gorgonzola dolce y nueces tostadas con reducción de balsámico",
         "precio": 7200},
    # Principales (9-20): pastas + pescados + carnes
    9:  {"nombre": "Tagliolini al nero di seppia",
         "descripcion": "Pasta negra al tintero, vieiras de Santa Cruz, ajo negro fermentado y bottarga rallada",
         "precio": 24500},
    10: {"nombre": "Pappardelle al ragú de ciervo",
         "descripcion": "Pappardelle de harina integral, ragú estofado 8 horas de ciervo patagónico, ricotta ahumada y hierbas del campo",
         "precio": 26800},
    11: {"nombre": "Risotto al tartufo bianco",
         "descripcion": "Arroz carnaroli, manteca clarificada, parmesano, aceite de trufa blanca y láminas de trufa fresca",
         "precio": 28000},
    12: {"nombre": "Ravioles de ricotta y espinaca",
         "descripcion": "Pasta fresca rellena, mantequilla noisette, salvia frita y nueces de pecan tostadas",
         "precio": 21500},
    13: {"nombre": "Merluza negra a la plancha",
         "descripcion": "Filete de merluza negra del sur, purée de coliflor ahumada, emulsión de azafrán y microgreens",
         "precio": 38500},
    14: {"nombre": "Salmón del Atlántico en papillote",
         "descripcion": "Filete de salmón con vegetales de estación, vino blanco, limón y hierbas provenzales",
         "precio": 32000},
    15: {"nombre": "Langostinos al ajillo con pasta de tinta",
         "descripcion": "Langostinos XL salteados en manteca con ajo, guindilla y fideos negros al dente",
         "precio": 34800},
    16: {"nombre": "Pulpo a la gallega",
         "descripcion": "Pulpo del Pacífico, pimentón ahumado de la Vera, aceite de oliva, sal gruesa y cachelos",
         "precio": 29500},
    17: {"nombre": "Bife de chorizo dry-aged 400g",
         "descripcion": "Madurado 45 días, grilla de leña, chimichurri clásico de la casa y papas rústicas con romero",
         "precio": 46000},
    18: {"nombre": "Lomo Wellington",
         "descripcion": "Lomo de ternera en costra de hojaldre, duxelles de hongos, jamón ibérico y salsa périgueux",
         "precio": 54500},
    19: {"nombre": "Costillar de cordero patagónico",
         "descripcion": "Rack de cordero con costra de hierbas, puré de batata y salsa de menta fresca",
         "precio": 48000},
    20: {"nombre": "Entrecot de wagyu A4 250g",
         "descripcion": "Wagyu importado de Japón, salsa de tuétano y vino tinto, espárragos a la parrilla y papas suflé",
         "precio": 68000},
    # Postres (21-25)
    21: {"nombre": "Brownie de chocolate con helado de vainilla",
         "descripcion": "Brownie húmedo de chocolate amargo, helado de vainilla de Tahití y salsa de caramelo salado",
         "precio": 12000},
    22: {"nombre": "Soufflé de chocolate amargo",
         "descripcion": "Soufflé caliente de chocolate amargo 72%, helado de vainilla de Tahití. Preparación: 18 min",
         "precio": 14500},
    23: {"nombre": "Tarte Tatin de manzanas",
         "descripcion": "Manzana Granny Smith caramelizada, masa hojaldrada invertida, crème fraîche y caramelo salado",
         "precio": 12800},
    24: {"nombre": "Crème brûlée de lavanda",
         "descripcion": "Crema de vainilla infusionada en lavanda de Mendoza, azúcar tostado al momento",
         "precio": 11200},
    25: {"nombre": "Tabla de quesos maduros",
         "descripcion": "Selección de 5 quesos, mermelada de higos, miel de colmena y crackers artesanales",
         "precio": 18000},
    # Bebidas (26-30)
    26: {"nombre": "Agua mineral 750ml",
         "descripcion": "Agua mineral sin gas / con gas",
         "precio": 4200},
    27: {"nombre": "Vino por copa — selección del sommelier",
         "descripcion": None,
         "precio": 9500},
    28: {"nombre": "Champagne Moët & Chandon Brut (copa)",
         "descripcion": None,
         "precio": 16000},
    29: {"nombre": "Gaseosa 350ml",
         "descripcion": "Selección de gaseosas importadas",
         "precio": 3500},
    30: {"nombre": "Cerveza artesanal",
         "descripcion": "Selección de cervezas artesanales nacionales e importadas",
         "precio": 6500},
}

ML_BACKEND = os.getenv("ML_BACKEND", "local")
SAGEMAKER_ENDPOINT = os.getenv("SAGEMAKER_ENDPOINT", "bistrotech-endpoint-v1")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
MODELO_VERSION = os.getenv("MODELO_VERSION", "v1.0")


# ── Enriquecimiento desde clientes_historico ───────────────────────────────

def enrich_comensales(req: PredictRequest) -> None:
    """
    Resuelve es_repetidor, visitas_previas y ticket_promedio_historico
    consultando clientes_historico por id_cliente (DNI).
    Muta el objeto req directamente — debe llamarse antes de run_inference.
    Walk-ins (id_cliente=None) → es_repetidor=False, visitas_previas=0.
    """
    for c in req.comensales:
        if c.id_cliente is not None:
            perfil = get_cliente_historico(c.id_cliente)
            if perfil:
                visitas = int(perfil.get("visitas_totales", 0))
                c.es_repetidor = visitas > 0
                c.visitas_previas = visitas
                if c.ticket_promedio_historico is None and perfil.get("ticket_promedio") is not None:
                    c.ticket_promedio_historico = float(perfil["ticket_promedio"])
                logger.debug("Cliente id=%s enriquecido: visitas=%s repetidor=%s",
                             c.id_cliente, visitas, c.es_repetidor)
            else:
                c.es_repetidor = False
                c.visitas_previas = 0
                logger.debug("Cliente id=%s sin historial — cold start", c.id_cliente)
        else:
            c.es_repetidor = False
            c.visitas_previas = 0


# ── Imputación de ticket via DynamoDB ──────────────────────────────────────

def _resolver_ticket(comensal, req) -> float | None:
    """
    Orden de prioridad para imputar ticket_promedio_historico cuando es None:
      1. Historial del cliente en DynamoDB (clientes_historico)
      2. Media del segmento (segmentos_referencia) — cold start
      3. None → el modelo maneja el fallback internamente
    """
    if comensal.id_cliente is not None:
        perfil = get_cliente_historico(comensal.id_cliente)
        if perfil and perfil.get("ticket_promedio") is not None:
            logger.debug("Ticket imputado desde clientes_historico id=%s", comensal.id_cliente)
            return float(perfil["ticket_promedio"])

    segmento = get_segmento_referencia(
        comensal.franja_etaria_persona.value,
        req.franja_horaria.value,
        comensal.motivo_visita.value,
    )
    if segmento and segmento.get("ticket_promedio_segmento") is not None:
        logger.debug("Ticket imputado desde segmento %s#%s#%s",
                     comensal.franja_etaria_persona.value,
                     req.franja_horaria.value,
                     comensal.motivo_visita.value)
        return float(segmento["ticket_promedio_segmento"])

    return None


# ── Helpers de feature engineering ─────────────────────────────────────────

def _dia_a_ciclico(dia: int) -> dict:
    """Convierte dia_semana a seno/coseno para que domingo y lunes sean 'cercanos'."""
    import math
    angulo = 2 * math.pi * dia / 7
    return {"dia_semana_sin": round(math.sin(angulo), 6),
            "dia_semana_cos": round(math.cos(angulo), 6)}


def _build_payload(req: PredictRequest) -> dict:
    """
    Construye el payload que espera el modelo.
    Nunca incluye columnas de feedback (like_*, propina_rate, etc.) — solo features.
    """
    ciclico = _dia_a_ciclico(req.dia_semana)
    comensales_payload = []

    for c in req.comensales:
        ticket = c.ticket_promedio_historico

        if ticket is None:
            ticket = _resolver_ticket(c, req)

        comensales_payload.append({
            "id_persona_en_mesa": c.id_persona_en_mesa,
            "franja_etaria_persona": c.franja_etaria_persona.value,
            "cant_acompanantes": c.cant_acompanantes,
            "viene_solo": c.cant_acompanantes == 0,
            "motivo_visita": c.motivo_visita.value,
            "restriccion_alimentaria": c.restriccion_alimentaria.value,
            "es_repetidor": c.es_repetidor,
            "visitas_previas_log1p": round(__import__("math").log1p(c.visitas_previas), 6),
            "ticket_promedio_historico": ticket,
            "orden_de_pedido": c.orden_de_pedido,
            **ciclico,
            "franja_horaria": req.franja_horaria.value,
        })

    return {
        "id_mesa": req.id_mesa,
        "comensales": comensales_payload,
    }


# ── Cliente SageMaker ───────────────────────────────────────────────────────

def _call_sagemaker(payload: dict) -> dict:
    client = boto3.client("sagemaker-runtime", region_name=AWS_REGION)
    response = client.invoke_endpoint(
        EndpointName=SAGEMAKER_ENDPOINT,
        ContentType="application/json",
        Body=json.dumps(payload),
    )
    return json.loads(response["Body"].read())


# ── Fallback local (desarrollo sin credenciales AWS) ───────────────────────

def _fallback_local(req: PredictRequest) -> dict:
    """
    Respuesta determinística para desarrollo local.
    Replica exactamente la estructura que devuelve el modelo real.
    """
    mozos = [
        {"id_mozo": i, "propina_rate_esperado": round(0.5 - i * 0.001, 4), "rank": i}
        for i in range(1, 9)
    ]

    recomendaciones = []
    for c in req.comensales:
        recomendaciones.append({
            "id_persona_en_mesa": c.id_persona_en_mesa,
            "entrada":    [{"id_plato": p, "score": round(0.13 - i * 0.005, 4), "rank": i + 1}
                           for i, p in enumerate([4, 8, 5])],
            "principal":  [{"id_plato": p, "score": round(0.12 - i * 0.01,  4), "rank": i + 1}
                           for i, p in enumerate([18, 17, 13])],
            "postre":     [{"id_plato": p, "score": round(0.22 - i * 0.02,  4), "rank": i + 1}
                           for i, p in enumerate([24, 22, 23])],
            "bebida":     [{"id_plato": p, "score": round(0.21 - i * 0.005, 4), "rank": i + 1}
                           for i, p in enumerate([30, 28, 29])],
        })

    return {
        "id_mesa": req.id_mesa,
        "mozos_recomendados": mozos,
        "recomendaciones_por_comensal": recomendaciones,
        "modelo_version": MODELO_VERSION,
    }


# ── Staff — mapeo id_mozo → nombre ────────────────────────────────────────
MOZOS: dict[int, str] = {
    1: "Valentín Herrera",
    2: "Lucía Morán",
    3: "Sebastián Ríos",
    4: "Camila Fontana",
    5: "Nicolás Paredes",
    6: "Sofía Villalba",
    7: "Matías Guerrero",
    8: "Florencia Castillo",
}


# ── Enriquecimiento de nombres de platos ───────────────────────────────────

def _enrich_nombres(raw: dict) -> None:
    """Agrega nombre_plato, descripcion y precio a cada plato en la respuesta (muta raw)."""
    for rec in raw.get("recomendaciones_por_comensal", []):
        for categoria in ("entrada", "principal", "postre", "bebida"):
            for plato in rec.get(categoria, []):
                info = PLATOS.get(plato["id_plato"], {})
                plato["nombre_plato"] = info.get("nombre", "Plato desconocido")
                plato["descripcion"] = info.get("descripcion")
                plato["precio"] = info.get("precio", 0)


def _enrich_mozos(raw: dict) -> None:
    """Agrega nombre al campo nombre_mozo en cada mozo recomendado (muta raw)."""
    for mozo in raw.get("mozos_recomendados", []):
        mozo["nombre_mozo"] = MOZOS.get(mozo["id_mozo"], "Mozo desconocido")


# ── Punto de entrada público ────────────────────────────────────────────────

def run_inference(req: PredictRequest) -> PredictResponse:
    enrich_comensales(req)
    t0 = time.monotonic()

    payload = _build_payload(req)

    if ML_BACKEND == "sagemaker":
        try:
            logger.info("Llamando SageMaker endpoint: %s", SAGEMAKER_ENDPOINT)
            raw = _call_sagemaker(payload)
        except (BotoCoreError, ClientError) as exc:
            logger.error("Error SageMaker: %s — usando fallback local", exc)
            raw = _fallback_local(req)
    else:
        logger.debug("ML_BACKEND=local — usando fallback determinístico")
        raw = _fallback_local(req)

    latencia_ms = int((time.monotonic() - t0) * 1000)
    _enrich_nombres(raw)
    _enrich_mozos(raw)

    return PredictResponse(
        id_mesa=raw["id_mesa"],
        mozos_recomendados=raw["mozos_recomendados"],
        recomendaciones_por_comensal=raw["recomendaciones_por_comensal"],
        modelo_version=raw.get("modelo_version", MODELO_VERSION),
        latencia_ms=latencia_ms,
    )
