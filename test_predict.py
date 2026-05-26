"""
Tests del backend BistroTech.
Correr con: python -m pytest test_predict.py -v
"""

import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

# ── Fixture de comensal base ────────────────────────────────────────────────

@pytest.fixture
def comensal_base():
    return {
        "id_persona_en_mesa": 1,
        "id_cliente": 12345678,
        "franja_etaria_persona": "adulto",
        "cant_acompanantes": 3,
        "motivo_visita": "negocios",
        "restriccion_alimentaria": "ninguna",
        "orden_de_pedido": 1,
    }


@pytest.fixture
def pedido_payload(comensal_base):
    return {
        "comensales": [comensal_base],
        "dia_semana": 1,
        "franja_horaria": "mediodia",
    }


@pytest.fixture
def feedback_payload():
    return {
        "comensales": [{
            "id_persona_en_mesa": 1,
            "id_mozo": 3,
            "id_principal": 12,
            "id_bebida": 27,
            "id_entrada": 4,
            "propina_rate": 0.15,
            "like_mozo": True,
            "like_principal": True,
            "proporcion_dejada_principal": "nada",
        }]
    }


# ── Tests ───────────────────────────────────────────────────────────────────

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_crear_pedido_happy_path(pedido_payload):
    r = client.post("/api/v1/mesas/42/pedidos", json=pedido_payload)
    assert r.status_code == 201
    body = r.json()
    assert body["id_mesa"] == 42
    assert "id_pedido" in body
    assert body["estado"] == "activo"
    assert len(body["mozos_recomendados"]) == 8
    assert len(body["recomendaciones_por_comensal"]) == 1
    comensal = body["recomendaciones_por_comensal"][0]
    for curso in ("entrada", "principal", "postre", "bebida"):
        assert len(comensal[curso]) == 3


def test_crear_pedido_cold_start(pedido_payload):
    """ticket_promedio_historico nulo — el modelo imputa por segmento."""
    pedido_payload["comensales"][0]["ticket_promedio_historico"] = None
    r = client.post("/api/v1/mesas/42/pedidos", json=pedido_payload)
    assert r.status_code == 201


def test_crear_pedido_multiples_comensales(comensal_base):
    payload = {
        "comensales": [
            {**comensal_base, "id_persona_en_mesa": i, "orden_de_pedido": i, "cant_acompanantes": 2}
            for i in range(1, 4)
        ],
        "dia_semana": 1,
        "franja_horaria": "noche",
    }
    r = client.post("/api/v1/mesas/42/pedidos", json=payload)
    assert r.status_code == 201
    assert len(r.json()["recomendaciones_por_comensal"]) == 3


def test_crear_pedido_dia_semana_invalido(pedido_payload):
    pedido_payload["dia_semana"] = 7
    r = client.post("/api/v1/mesas/42/pedidos", json=pedido_payload)
    assert r.status_code == 422



def test_crear_pedido_latencia_presente(pedido_payload):
    r = client.post("/api/v1/mesas/42/pedidos", json=pedido_payload)
    assert r.status_code == 201
    assert r.json()["latencia_ms"] >= 0


def test_obtener_pedido_no_existe():
    """Sin DynamoDB disponible, siempre devuelve 404."""
    r = client.get("/api/v1/pedidos/uuid-inexistente")
    assert r.status_code == 404


def test_feedback_pedido_no_existe(feedback_payload):
    """Sin DynamoDB disponible, siempre devuelve 404."""
    r = client.post("/api/v1/pedidos/uuid-inexistente/feedback", json=feedback_payload)
    assert r.status_code == 404
