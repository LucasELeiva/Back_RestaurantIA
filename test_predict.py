"""
Tests del backend BistroTech.
Correr con: python -m pytest test_predict.py -v
"""

import pytest
from types import SimpleNamespace
from fastapi.testclient import TestClient
from app.main import app
from app.models.schemas import EstadoMesa

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


def registrar_preferencia(id_mesa: int, comensal: dict) -> dict:
    r = client.post(
        "/api/v1/pedidos/preferencias",
        json={"id_mesa": id_mesa, "comensal": comensal},
    )
    assert r.status_code == 201
    return r.json()


def crear_pedido_mesa(id_mesa: int, dia_semana: int = 1, franja_horaria: str = "mediodia"):
    return client.post(
        f"/api/v1/pedidos/pedidos/{id_mesa}",
        json={"dia_semana": dia_semana, "franja_horaria": franja_horaria},
    )


# ── Tests ───────────────────────────────────────────────────────────────────

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_listar_mesas_incluye_cantidad_personas(monkeypatch):
    class FakeMesaTable:
        def scan(self, **kwargs):
            return {
                "Items": [{
                    "id_mesa": 1,
                    "capacidad": 2,
                    "ubicacion": "salon",
                    "estado": "libre",
                    "activa": True,
                }]
            }

    monkeypatch.setattr("app.services.mesa_service._get_table", lambda table_name="": FakeMesaTable())
    monkeypatch.setattr(
        "app.services.mesa_service._contar_personas_por_mesa",
        lambda: {1: 2},
    )

    r = client.get("/api/v1/mesas?solo_activas=true")
    assert r.status_code == 200
    body = r.json()
    assert body[0]["id_mesa"] == 1
    assert body[0]["cantidad_personas"] == 2


def test_crear_pedido_happy_path(pedido_payload):
    preferencia = registrar_preferencia(421, pedido_payload["comensales"][0])
    r = crear_pedido_mesa(421)
    assert r.status_code == 201
    body = r.json()
    assert body["id_mesa"] == 421
    assert "id_pedido" in body
    assert body["estado"] == "activo"
    assert len(body["mozos_recomendados"]) == 8
    assert len(body["recomendaciones_por_comensal"]) == 1
    comensal = body["recomendaciones_por_comensal"][0]
    for curso in ("entrada", "principal", "postre", "bebida"):
        assert len(comensal[curso]) == 3

    estado = client.get(f"/api/v1/pedidos/preferencias/{preferencia['codigo_pedido']}")
    assert estado.status_code == 200
    assert estado.json()["estado"] == "activo"
    assert estado.json()["id_pedido"] == body["id_pedido"]


def test_crear_pedido_cold_start(pedido_payload):
    """ticket_promedio_historico nulo — el modelo imputa por segmento."""
    pedido_payload["comensales"][0]["ticket_promedio_historico"] = None
    registrar_preferencia(422, pedido_payload["comensales"][0])
    r = crear_pedido_mesa(422)
    assert r.status_code == 201


def test_crear_pedido_multiples_comensales(comensal_base):
    id_mesa = 423
    payload = {
        "comensales": [
            {**comensal_base, "id_persona_en_mesa": i, "orden_de_pedido": i, "cant_acompanantes": 2}
            for i in range(1, 4)
        ],
        "dia_semana": 1,
        "franja_horaria": "noche",
    }
    for comensal in payload["comensales"]:
        registrar_preferencia(id_mesa, comensal)
    r = crear_pedido_mesa(id_mesa, franja_horaria="noche")
    assert r.status_code == 201
    assert len(r.json()["recomendaciones_por_comensal"]) == 3


def test_crear_pedido_dia_semana_invalido(pedido_payload):
    registrar_preferencia(424, pedido_payload["comensales"][0])
    r = crear_pedido_mesa(424, dia_semana=7)
    assert r.status_code == 422



def test_crear_pedido_latencia_presente(pedido_payload):
    registrar_preferencia(425, pedido_payload["comensales"][0])
    r = crear_pedido_mesa(425)
    assert r.status_code == 201
    assert r.json()["latencia_ms"] >= 0


def test_registrar_preferencia_y_consultar_estado(comensal_base):
    body = registrar_preferencia(426, comensal_base)
    assert body["estado"] == "pendiente"
    assert "codigo_pedido" in body

    r = client.get(f"/api/v1/pedidos/preferencias/{body['codigo_pedido']}")
    assert r.status_code == 200
    assert r.json()["codigo_pedido"] == body["codigo_pedido"]


def test_registrar_preferencia_calcula_id_persona_en_mesa(comensal_base):
    comensal_sin_posicion = {
        key: value
        for key, value in comensal_base.items()
        if key != "id_persona_en_mesa"
    }

    primero = registrar_preferencia(428, comensal_sin_posicion)
    segundo = registrar_preferencia(428, comensal_sin_posicion)

    assert primero["comensal"]["id_persona_en_mesa"] == 1
    assert segundo["comensal"]["id_persona_en_mesa"] == 2


def test_registrar_preferencia_ignora_id_persona_en_mesa_enviado(comensal_base):
    primero = registrar_preferencia(429, {**comensal_base, "id_persona_en_mesa": 99})
    segundo = registrar_preferencia(429, {**comensal_base, "id_persona_en_mesa": 100})

    assert primero["comensal"]["id_persona_en_mesa"] == 1
    assert segundo["comensal"]["id_persona_en_mesa"] == 2


def test_registrar_preferencia_marca_mesa_ocupada_si_se_llena(monkeypatch, comensal_base):
    updates = []

    def fake_get_mesa(id_mesa):
        return SimpleNamespace(
            id_mesa=id_mesa,
            capacidad=2,
            estado=EstadoMesa.libre,
        )

    def fake_update_mesa(id_mesa, req):
        updates.append((id_mesa, req.estado))
        return None

    monkeypatch.setattr("app.services.pedido_service.get_mesa", fake_get_mesa)
    monkeypatch.setattr("app.services.pedido_service.update_mesa", fake_update_mesa)

    registrar_preferencia(432, comensal_base)
    assert updates == []

    registrar_preferencia(432, comensal_base)
    assert updates == [(432, EstadoMesa.ocupada)]


def test_listar_platos_recomendados_por_codigo(comensal_base):
    preferencia = registrar_preferencia(430, comensal_base)
    crear_pedido_mesa(430)

    r = client.get(f"/api/v1/pedidos/preferencias/{preferencia['codigo_pedido']}/platos")
    assert r.status_code == 200
    body = r.json()
    assert body["id_persona_en_mesa"] == preferencia["comensal"]["id_persona_en_mesa"]
    for curso in ("entrada", "principal", "postre", "bebida"):
        assert len(body[curso]) == 3


def test_mozo_informa_y_usuario_elige_platos(comensal_base):
    preferencia = registrar_preferencia(427, comensal_base)
    pedido = crear_pedido_mesa(427).json()

    r = client.patch(
        f"/api/v1/pedidos/{pedido['id_pedido']}/estado",
        json={"estado": "informado"},
    )
    assert r.status_code == 200
    assert r.json()["estado"] == "informado"

    r = client.post(
        f"/api/v1/pedidos/preferencias/{preferencia['codigo_pedido']}/platos",
        json={
            "id_entrada": 2,
            "id_principal": 10,
            "id_postre": 22,
            "id_bebida": 27,
        },
    )
    assert r.status_code == 200
    assert r.json()["estado"] == "confirmado"

    estado = client.get(f"/api/v1/pedidos/preferencias/{preferencia['codigo_pedido']}")
    assert estado.status_code == 200
    assert estado.json()["estado"] == "confirmado"
    assert estado.json()["platos_seleccionados"]["id_principal"] == 10


def test_finalizar_comida_por_codigo_envia_feedback(comensal_base):
    preferencia = registrar_preferencia(431, comensal_base)
    crear_pedido_mesa(431)

    r = client.post(
        f"/api/v1/pedidos/preferencias/{preferencia['codigo_pedido']}/feedback",
        json={
            "id_mozo": 3,
            "id_entrada": 2,
            "id_principal": 10,
            "id_postre": 22,
            "id_bebida": 27,
            "propina_rate": 0.12,
            "like_mozo": True,
            "like_principal": True,
            "proporcion_dejada_principal": "nada",
        },
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["registros_actualizados"] == 1

    estado = client.get(f"/api/v1/pedidos/preferencias/{preferencia['codigo_pedido']}")
    assert estado.status_code == 200
    assert estado.json()["estado"] == "cerrado"


def test_obtener_pedido_no_existe():
    """Sin DynamoDB disponible, siempre devuelve 404."""
    r = client.get("/api/v1/pedidos/uuid-inexistente")
    assert r.status_code == 404


def test_feedback_pedido_no_existe(feedback_payload):
    """Sin DynamoDB disponible, siempre devuelve 404."""
    r = client.post("/api/v1/pedidos/uuid-inexistente/feedback", json=feedback_payload)
    assert r.status_code == 404
