from pydantic import BaseModel, Field, field_validator
from pydantic.json_schema import SkipJsonSchema
from typing import Optional, Union
from enum import Enum


# ── Mapas de normalización (acepta variantes del front) ─────────────────────

_MOTIVO_MAP: dict[str, str] = {
    "negocios": "negocios", "reunion": "negocios", "reunión": "negocios",
    "casual": "casual", "cena casual": "casual",
    "cumpleaños": "cumpleaños", "cumpleanos": "cumpleaños",
    "celebración": "cumpleaños", "celebracion": "cumpleaños",
    "date": "date", "aniversario": "date",
    "noche romántica": "date", "noche romantica": "date", "cita": "date",
    "turista": "turista",
}

_RESTRICCION_MAP: dict[str, str] = {
    "ninguna": "ninguna", "sin lactosa": "ninguna",
    "alergia a frutos secos": "ninguna", "nut allergy": "ninguna",
    "dairy-free": "ninguna", "none / eat everything": "ninguna",
    "vegetariano": "vegetariano", "vegetariana": "vegetariano", "vegetarian": "vegetariano",
    "vegano": "vegano", "vegana": "vegano", "vegan": "vegano",
    "celiaco": "celiaco", "sin gluten": "celiaco",
    "gluten-free": "celiaco", "gluten free": "celiaco",
    "kosher": "kosher",
}

_FRANJA_ETARIA_MAP: dict[str, str] = {
    "joven": "joven", "20s": "joven", "30s": "joven",
    "adulto": "adulto", "40s": "adulto", "50s": "adulto", "mixto": "adulto",
    "senior": "senior", "60s": "senior",
}


def _norm_motivo(v: object) -> object:
    if isinstance(v, str):
        return _MOTIVO_MAP.get(v.lower().strip(), v)
    return v

def _norm_restriccion(v: object) -> object:
    if isinstance(v, str):
        return _RESTRICCION_MAP.get(v.lower().strip(), v)
    return v

def _norm_franja_etaria(v: object) -> object:
    if isinstance(v, str):
        return _FRANJA_ETARIA_MAP.get(v.lower().strip(), v)
    return v

def _parse_dni(v: object) -> object:
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return v


# ── Enums según el schema definido en el resumen ejecutivo ──────────────────

class FranjaEtaria(str, Enum):
    joven = "joven"
    adulto = "adulto"
    senior = "senior"

class MotivoVisita(str, Enum):
    cumpleanos = "cumpleaños"
    negocios = "negocios"
    casual = "casual"
    date = "date"
    turista = "turista"

class RestriccionAlimentaria(str, Enum):
    ninguna = "ninguna"
    vegetariano = "vegetariano"
    vegano = "vegano"
    celiaco = "celiaco"
    kosher = "kosher"

class FranjaHoraria(str, Enum):
    mediodia = "mediodia"
    tarde = "tarde"
    noche = "noche"

class ProporcionDejada(str, Enum):
    nada = "nada"
    poco = "poco"
    mitad = "mitad"
    mayoria = "mayoria"
    todo = "todo"

class UbicacionMesa(str, Enum):
    salon = "salon"
    privado = "privado"

class EstadoMesa(str, Enum):
    libre = "libre"
    ocupada = "ocupada"
    reservada = "reservada"

class EstadoReserva(str, Enum):
    confirmada = "confirmada"
    cancelada = "cancelada"
    completada = "completada"


# ── Input ───────────────────────────────────────────────────────────────────

class Comensal(BaseModel):
    id_persona_en_mesa: int = Field(..., ge=1, description="Posición dentro de la mesa")
    id_cliente: Optional[int] = Field(None, description="DNI del cliente. Null para walk-ins.")
    franja_etaria_persona: FranjaEtaria
    cant_acompanantes: int = Field(..., ge=0)
    motivo_visita: MotivoVisita
    restriccion_alimentaria: RestriccionAlimentaria
    orden_de_pedido: int = Field(..., ge=1)
    # Resueltos internamente desde clientes_historico — ocultos del schema público
    es_repetidor: SkipJsonSchema[bool] = False
    visitas_previas: SkipJsonSchema[int] = 0
    ticket_promedio_historico: SkipJsonSchema[Optional[float]] = None

    @field_validator("id_cliente", mode="before")
    @classmethod
    def parsear_dni(cls, v): return _parse_dni(v)

    @field_validator("franja_etaria_persona", mode="before")
    @classmethod
    def normalizar_franja(cls, v): return _norm_franja_etaria(v)

    @field_validator("motivo_visita", mode="before")
    @classmethod
    def normalizar_motivo(cls, v): return _norm_motivo(v)

    @field_validator("restriccion_alimentaria", mode="before")
    @classmethod
    def normalizar_restriccion(cls, v): return _norm_restriccion(v)


class PredictRequest(BaseModel):
    id_mesa: int = Field(..., ge=1)
    comensales: list[Comensal] = Field(..., min_length=1, max_length=20)
    dia_semana: int = Field(..., ge=0, le=6, description="0=Lunes, 6=Domingo")
    franja_horaria: FranjaHoraria


# ── Output ──────────────────────────────────────────────────────────────────

class MozoRecomendado(BaseModel):
    id_mozo: int
    nombre_mozo: Optional[str] = None
    propina_rate_esperado: float
    rank: int

class Plato(BaseModel):
    id_plato: int
    nombre_plato: str
    descripcion: Optional[str]
    precio: int
    score: float
    rank: int

class RecomendacionComensal(BaseModel):
    id_persona_en_mesa: int
    entrada: list[Plato]
    principal: list[Plato]
    postre: list[Plato]
    bebida: list[Plato]

class PredictResponse(BaseModel):
    id_mesa: int
    mozos_recomendados: list[MozoRecomendado]
    recomendaciones_por_comensal: list[RecomendacionComensal]
    modelo_version: str
    latencia_ms: int


# ── Mesas ───────────────────────────────────────────────────────────────────

class MesaCreate(BaseModel):
    id_mesa: int = Field(..., ge=1)
    capacidad: int = Field(..., ge=1, le=20)
    ubicacion: UbicacionMesa

class MesaUpdate(BaseModel):
    capacidad: Optional[int] = Field(None, ge=1, le=20)
    ubicacion: Optional[UbicacionMesa] = None
    estado: Optional[EstadoMesa] = None
    activa: Optional[bool] = None

class Mesa(BaseModel):
    id_mesa: int
    capacidad: int
    ubicacion: UbicacionMesa
    estado: EstadoMesa
    activa: bool


# ── Reservas ─────────────────────────────────────────────────────────────────

class ReservaCreate(BaseModel):
    id_mesa: int = Field(..., ge=1)
    nombre_cliente: str = Field(..., min_length=1)
    id_cliente: Optional[int] = None
    fecha_hora: str = Field(..., description="ISO 8601 — ej: 2024-03-15T20:00:00")
    cantidad_personas: int = Field(..., ge=1, le=20)
    motivo_visita: Optional[MotivoVisita] = None
    notas: Optional[str] = None
    email: Optional[str] = None
    telefono: Optional[str] = None

    @field_validator("id_cliente", mode="before")
    @classmethod
    def parsear_dni(cls, v): return _parse_dni(v)

    @field_validator("motivo_visita", mode="before")
    @classmethod
    def normalizar_motivo(cls, v): return _norm_motivo(v)

class ReservaUpdate(BaseModel):
    fecha_hora: Optional[str] = None
    cantidad_personas: Optional[int] = Field(None, ge=1, le=20)
    motivo_visita: Optional[MotivoVisita] = None
    estado: Optional[EstadoReserva] = None
    notas: Optional[str] = None

class Reserva(BaseModel):
    id_reserva: str
    id_mesa: int
    nombre_cliente: str
    id_cliente: Optional[int]
    fecha_hora: str
    cantidad_personas: int
    motivo_visita: Optional[str]
    estado: EstadoReserva
    notas: Optional[str]
    email: Optional[str]
    telefono: Optional[str]
    created_at: str


# ── Feedback post-servicio (viene del POS) ──────────────────────────────────

class FeedbackRequest(BaseModel):
    id_mesa: int = Field(..., ge=1)
    id_persona_en_mesa: int = Field(..., ge=1)
    # Servicio real
    id_mozo: int = Field(..., ge=1, le=8)
    id_entrada: Optional[int] = Field(None, ge=1, le=8)
    id_principal: int = Field(..., ge=9, le=20)
    id_postre: Optional[int] = Field(None, ge=21, le=25)
    id_bebida: int = Field(..., ge=26, le=30)
    hora_entrega_plato: Optional[str] = None
    hora_retiro_plato: Optional[str] = None
    # Propina
    monto_propina: Optional[float] = Field(None, ge=0)
    propina_rate: Optional[float] = Field(None, ge=0)
    # Likes explícitos
    like_mozo: Optional[bool] = None
    like_entrada: Optional[bool] = None
    like_principal: Optional[bool] = None
    like_postre: Optional[bool] = None
    like_bebida: Optional[bool] = None
    # Proporción dejada en el plato
    proporcion_dejada_entrada: Optional[ProporcionDejada] = None
    proporcion_dejada_principal: Optional[ProporcionDejada] = None
    proporcion_dejada_postre: Optional[ProporcionDejada] = None


class FeedbackResponse(BaseModel):
    ok: bool
    id_mesa: int
    id_persona_en_mesa: int
    registros_actualizados: int


# ── Pedidos ──────────────────────────────────────────────────────────────────

class EstadoPedido(str, Enum):
    activo = "activo"
    cerrado = "cerrado"

class PedidoCreate(BaseModel):
    comensales: list[Comensal] = Field(..., min_length=1, max_length=20)
    dia_semana: int = Field(..., ge=0, le=6, description="0=Lunes, 6=Domingo")
    franja_horaria: FranjaHoraria

class PedidoResponse(BaseModel):
    id_pedido: str
    id_mesa: int
    estado: EstadoPedido
    fecha_hora: str
    mozos_recomendados: list[MozoRecomendado]
    recomendaciones_por_comensal: list[RecomendacionComensal]
    modelo_version: str
    latencia_ms: int

class ComensalFeedback(BaseModel):
    id_persona_en_mesa: int = Field(..., ge=1)
    id_mozo: int = Field(..., ge=1, le=8)
    id_entrada: Optional[int] = Field(None, ge=1, le=8)
    id_principal: int = Field(..., ge=9, le=20)
    id_postre: Optional[int] = Field(None, ge=21, le=25)
    id_bebida: int = Field(..., ge=26, le=30)
    hora_entrega_plato: Optional[str] = None
    hora_retiro_plato: Optional[str] = None
    monto_propina: Optional[float] = Field(None, ge=0)
    propina_rate: Optional[float] = Field(None, ge=0)
    like_mozo: Optional[bool] = None
    like_entrada: Optional[bool] = None
    like_principal: Optional[bool] = None
    like_postre: Optional[bool] = None
    like_bebida: Optional[bool] = None
    proporcion_dejada_entrada: Optional[ProporcionDejada] = None
    proporcion_dejada_principal: Optional[ProporcionDejada] = None
    proporcion_dejada_postre: Optional[ProporcionDejada] = None

class PedidoFeedbackRequest(BaseModel):
    comensales: list[ComensalFeedback] = Field(..., min_length=1)

class PedidoFeedbackResponse(BaseModel):
    ok: bool
    id_pedido: str
    registros_actualizados: int
