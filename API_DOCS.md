# BistroTech API — Documentación Técnica

**Base URL:** `http://localhost:8000`  
**Versión:** 1.0.0  
**Formato:** JSON en todos los requests y responses  
**Explorador interactivo:** `http://localhost:8000/docs` (Swagger UI)

---

## Índice

- [Enums y valores válidos](#enums-y-valores-válidos)
- [Mesas](#mesas)
- [Reservas](#reservas)
- [Pedidos](#pedidos)
- [Códigos de error comunes](#códigos-de-error-comunes)
- [Tablas DynamoDB](#tablas-dynamodb)

---

## Enums y valores válidos

El backend normaliza automáticamente los valores recibidos — no es necesario enviar el valor exacto del enum, se aceptan variantes en español, inglés y mayúsculas/minúsculas.

### `franja_etaria_persona`

| Valor canónico | También acepta |
|---|---|
| `"joven"` | `"20s"`, `"30s"` |
| `"adulto"` | `"40s"`, `"50s"`, `"Mixto"` |
| `"senior"` | `"60s"` |

### `motivo_visita`

| Valor canónico | También acepta |
|---|---|
| `"negocios"` | `"Negocios"`, `"Reunión"`, `"reunion"` |
| `"casual"` | `"Casual"`, `"Cena casual"` |
| `"cumpleaños"` | `"Cumpleaños"`, `"Celebración"`, `"celebracion"` |
| `"date"` | `"Aniversario"`, `"Noche romántica"`, `"Cita"` |
| `"turista"` | `"Turista"` |

### `restriccion_alimentaria`

| Valor canónico | También acepta |
|---|---|
| `"ninguna"` | `"Ninguna"`, `"Sin lactosa"`, `"Alergia a frutos secos"`, `"Nut Allergy"`, `"Dairy-Free"`, `"None / Eat Everything"` |
| `"vegetariano"` | `"Vegetariana"`, `"Vegetarian"` |
| `"vegano"` | `"Vegana"`, `"Vegan"` |
| `"celiaco"` | `"Sin gluten"`, `"Gluten-Free"` |
| `"kosher"` | `"Kosher"` |

### Otros enums (sin alias — enviar el valor exacto)

| Campo | Valores válidos |
|---|---|
| `ubicacion` | `"salon"`, `"privado"` |
| `estado` (mesa) | `"libre"`, `"ocupada"`, `"reservada"` |
| `estado` (reserva) | `"confirmada"`, `"cancelada"`, `"completada"` |
| `estado` (pedido) | `"activo"`, `"cerrado"` |
| `franja_horaria` | `"mediodia"`, `"tarde"`, `"noche"` |
| `proporcion_dejada_*` | `"nada"`, `"poco"`, `"mitad"`, `"mayoria"`, `"todo"` |

### `id_cliente` — DNI

Acepta tanto `integer` como `string` numérico. Ambas formas son equivalentes:
```json
{ "id_cliente": 12345678 }
{ "id_cliente": "12345678" }
```

**IDs de platos por curso:**

| Curso | Rango de IDs |
|---|---|
| Entradas | 1 – 8 |
| Principales | 9 – 20 |
| Postres | 21 – 25 |
| Bebidas | 26 – 30 |
| Mozos | 1 – 8 |

---

## Mesas

### `POST /api/v1/mesas`
Crea una nueva mesa física. Falla si ya existe una mesa con el mismo `id_mesa`.

**Request body:**
```json
{
  "id_mesa": 1,
  "capacidad": 4,
  "ubicacion": "salon"
}
```

| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `id_mesa` | integer ≥ 1 | Sí | ID único de la mesa |
| `capacidad` | integer 1–20 | Sí | Cantidad máxima de personas |
| `ubicacion` | enum | Sí | `"salon"` o `"privado"` |

**Response `201`:**
```json
{
  "id_mesa": 1,
  "capacidad": 4,
  "ubicacion": "salon",
  "estado": "libre",
  "activa": true
}
```

**Errores:** `409` si ya existe una mesa con ese `id_mesa`.

---

### `GET /api/v1/mesas`
Lista todas las mesas. Por defecto solo devuelve las activas.

**Query params:**

| Param | Tipo | Default | Descripción |
|---|---|---|---|
| `solo_activas` | boolean | `true` | `false` para incluir mesas eliminadas (soft delete) |

**Response `200`:** Array de objetos Mesa (mismo formato que el POST).

---

### `GET /api/v1/mesas/{id_mesa}`
Obtiene una mesa por su ID.

**Response `200`:**
```json
{
  "id_mesa": 1,
  "capacidad": 4,
  "ubicacion": "salon",
  "estado": "libre",
  "activa": true
}
```

**Errores:** `404` si no existe.

---

### `PATCH /api/v1/mesas/{id_mesa}`
Actualiza parcialmente una mesa. Solo se modifican los campos enviados.

**Request body** (todos opcionales):
```json
{
  "capacidad": 6,
  "ubicacion": "privado",
  "estado": "ocupada",
  "activa": true
}
```

**Response `200`:** Mesa actualizada (mismo formato que el GET).

**Errores:** `404` si no existe.

---

### `DELETE /api/v1/mesas/{id_mesa}`
Soft delete — marca la mesa como `activa: false`. No borra el registro.

**Response `204`:** Sin body.

**Errores:** `404` si no existe.

---

### `POST /api/v1/mesas/{id_mesa}/pedidos`
Crea un pedido para la mesa. Internamente llama al modelo de ML y devuelve las recomendaciones de mozos y platos.

> **Identificación de cliente:** el campo `id_cliente` es el **DNI** del cliente cargado en la página. El backend resuelve automáticamente el historial (visitas previas, ticket promedio) consultando `clientes_historico`. El front **no debe enviar** `es_repetidor`, `visitas_previas` ni `ticket_promedio_historico`.

**Request body:**
```json
{
  "comensales": [
    {
      "id_persona_en_mesa": 1,
      "id_cliente": 12345678,
      "franja_etaria_persona": "adulto",
      "cant_acompanantes": 3,
      "motivo_visita": "negocios",
      "restriccion_alimentaria": "ninguna",
      "orden_de_pedido": 1
    }
  ],
  "dia_semana": 1,
  "franja_horaria": "mediodia"
}
```

**Objeto Comensal:**

| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `id_persona_en_mesa` | integer ≥ 1 | Sí | Posición en la mesa (1, 2, 3...) |
| `id_cliente` | integer \| null | No | **DNI** del cliente. `null` para walk-ins sin identificar |
| `franja_etaria_persona` | enum | Sí | Edad aproximada del comensal |
| `cant_acompanantes` | integer ≥ 0 | Sí | Cuántas personas más vinieron con él |
| `motivo_visita` | enum | Sí | Propósito de la visita |
| `restriccion_alimentaria` | enum | Sí | Restricción dietaria |
| `orden_de_pedido` | integer ≥ 1 | Sí | Orden en que pidió dentro de la mesa (1 = primero) |

**Campos resueltos internamente — no enviar:**

| Campo | Cómo se resuelve |
|---|---|
| `es_repetidor` | `true` si el DNI tiene visitas registradas en `clientes_historico` |
| `visitas_previas` | `visitas_totales` de `clientes_historico`. `0` si es cliente nuevo |
| `ticket_promedio_historico` | `ticket_promedio` de `clientes_historico`. Si no hay historial, se imputa por segmento (`franja_etaria + franja_horaria + motivo_visita`) |

**Campos del pedido:**

| Campo | Tipo | Descripción |
|---|---|---|
| `comensales` | array (1–20) | Lista de comensales |
| `dia_semana` | integer 0–6 | 0 = Lunes, 6 = Domingo |
| `franja_horaria` | enum | Momento del día |

**Response `201`:**
```json
{
  "id_pedido": "uuid",
  "id_mesa": 1,
  "estado": "activo",
  "fecha_hora": "2024-03-15T20:00:00+00:00",
  "mozos_recomendados": [
    { "id_mozo": 3, "propina_rate_esperado": 0.18, "rank": 1 },
    { "id_mozo": 7, "propina_rate_esperado": 0.15, "rank": 2 }
  ],
  "recomendaciones_por_comensal": [
    {
      "id_persona_en_mesa": 1,
      "entrada":   [{ "id_plato": 4, "score": 0.87, "rank": 1 }, ...],
      "principal": [{ "id_plato": 18, "score": 0.91, "rank": 1 }, ...],
      "postre":    [{ "id_plato": 24, "score": 0.76, "rank": 1 }, ...],
      "bebida":    [{ "id_plato": 30, "score": 0.82, "rank": 1 }, ...]
    }
  ],
  "modelo_version": "v1.0",
  "latencia_ms": 42
}
```

Cada curso devuelve **top 3 platos** ordenados por `rank`. Los mozos también vienen ordenados por `rank` (mayor `propina_rate_esperado` primero).

---

### `GET /api/v1/mesas/{id_mesa}/pedidos`
Lista todos los pedidos de una mesa, ordenados por fecha descendente.

**Response `200`:** Array de objetos Pedido (mismo formato que el POST).

---

## Reservas

### `POST /api/v1/reservas`
Crea una reserva. El estado inicial siempre es `"confirmada"`.

> **`fecha_hora`:** el front debe combinar fecha + hora en formato ISO 8601: `fecha + "T" + hora + ":00"` → `"2024-03-15T20:00:00"`.

**Request body:**
```json
{
  "id_mesa": 1,
  "nombre_cliente": "Juan Pérez",
  "id_cliente": "12345678",
  "fecha_hora": "2024-03-15T20:00:00",
  "cantidad_personas": 4,
  "motivo_visita": "Cumpleaños",
  "notas": "Solicitudes especiales",
  "email": "juan@ejemplo.com",
  "telefono": "+54 911 234 5678"
}
```

| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `id_mesa` | integer ≥ 1 | Sí | Mesa a reservar |
| `nombre_cliente` | string | Sí | Nombre del titular |
| `id_cliente` | integer \| string \| null | No | **DNI** del cliente. Acepta string o integer. Null para anónimos |
| `fecha_hora` | string ISO 8601 | Sí | Combinar date + time: `"2024-03-15T20:00:00"` |
| `cantidad_personas` | integer 1–20 | Sí | |
| `motivo_visita` | enum \| null | No | Ver tabla de aliases arriba |
| `notas` | string \| null | No | Solicitudes especiales / observaciones |
| `email` | string \| null | No | Email de contacto del cliente |
| `telefono` | string \| null | No | Teléfono de contacto |

**Response `201`:**
```json
{
  "id_reserva": "uuid",
  "id_mesa": 1,
  "nombre_cliente": "Juan Pérez",
  "id_cliente": 12345678,
  "fecha_hora": "2024-03-15T20:00:00",
  "cantidad_personas": 4,
  "motivo_visita": "cumpleaños",
  "estado": "confirmada",
  "notas": "Solicitudes especiales",
  "email": "juan@ejemplo.com",
  "telefono": "+54 911 234 5678",
  "created_at": "2024-03-10T14:00:00+00:00"
}
```

---

### `GET /api/v1/reservas`
Lista reservas, ordenadas por fecha ascendente.

**Query params:**

| Param | Tipo | Descripción |
|---|---|---|
| `id_mesa` | integer | Filtra por mesa. Omitir para traer todas. |

**Response `200`:** Array de objetos Reserva.

---

### `GET /api/v1/reservas/{id_reserva}`
Obtiene una reserva por su UUID.

**Response `200`:** Objeto Reserva.

**Errores:** `404` si no existe.

---

### `PATCH /api/v1/reservas/{id_reserva}`
Actualiza parcialmente una reserva. Todos los campos son opcionales.

**Request body:**
```json
{
  "fecha_hora": "2024-03-16T21:00:00",
  "cantidad_personas": 5,
  "motivo_visita": "negocios",
  "estado": "completada",
  "notas": "Mesa con vista"
}
```

**Response `200`:** Reserva actualizada.

**Errores:** `404` si no existe.

---

### `DELETE /api/v1/reservas/{id_reserva}`
Cancela la reserva (cambia el estado a `"cancelada"`). No elimina el registro.

**Response `204`:** Sin body.

**Errores:** `404` si no existe.

---

## Pedidos

### `GET /api/v1/pedidos/{id_pedido}`
Obtiene un pedido por su UUID.

**Response `200`:** Objeto Pedido (mismo formato que `POST /mesas/{id}/pedidos`).

**Errores:** `404` si no existe.

---

### `POST /api/v1/pedidos/{id_pedido}/feedback`
Envía el feedback post-servicio desde el POS. Completa los datos del pedido en la base y lo marca como `"cerrado"`.

**Request body:**
```json
{
  "comensales": [
    {
      "id_persona_en_mesa": 1,
      "id_mozo": 3,
      "id_entrada": 4,
      "id_principal": 12,
      "id_postre": 22,
      "id_bebida": 27,
      "hora_entrega_plato": "20:15:00",
      "hora_retiro_plato": "20:45:00",
      "monto_propina": 500.0,
      "propina_rate": 0.15,
      "like_mozo": true,
      "like_entrada": true,
      "like_principal": true,
      "like_postre": null,
      "like_bebida": true,
      "proporcion_dejada_entrada": "nada",
      "proporcion_dejada_principal": "nada",
      "proporcion_dejada_postre": "mitad"
    }
  ]
}
```

**Campos por comensal:**

| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `id_persona_en_mesa` | integer ≥ 1 | Sí | Debe coincidir con el pedido original |
| `id_mozo` | integer 1–8 | Sí | Mozo que atendió |
| `id_principal` | integer 9–20 | Sí | Plato principal consumido |
| `id_bebida` | integer 26–30 | Sí | Bebida consumida |
| `id_entrada` | integer 1–8 \| null | No | |
| `id_postre` | integer 21–25 \| null | No | |
| `hora_entrega_plato` | string \| null | No | Formato `"HH:MM:SS"` |
| `hora_retiro_plato` | string \| null | No | Formato `"HH:MM:SS"` |
| `monto_propina` | float ≥ 0 \| null | No | Monto absoluto |
| `propina_rate` | float ≥ 0 \| null | No | Proporción (0.15 = 15%) |
| `like_mozo` | boolean \| null | No | |
| `like_entrada` | boolean \| null | No | |
| `like_principal` | boolean \| null | No | |
| `like_postre` | boolean \| null | No | |
| `like_bebida` | boolean \| null | No | |
| `proporcion_dejada_entrada` | enum \| null | No | Qué proporción dejó sin comer |
| `proporcion_dejada_principal` | enum \| null | No | |
| `proporcion_dejada_postre` | enum \| null | No | |

**Response `200`:**
```json
{
  "ok": true,
  "id_pedido": "uuid",
  "registros_actualizados": 1
}
```

**Errores:** `404` si el pedido no existe.

---

## Códigos de error comunes

| Código | Significado |
|---|---|
| `422 Unprocessable Entity` | El body no cumple las validaciones (campo faltante, valor fuera de rango, enum inválido) |
| `404 Not Found` | El recurso no existe |
| `409 Conflict` | Intento de crear una mesa con un `id_mesa` ya existente |
| `500 Internal Server Error` | Error inesperado del servidor |

Los errores `422` incluyen detalle de qué campo falló:
```json
{
  "detail": [
    {
      "loc": ["body", "comensales", 0, "restriccion_alimentaria"],
      "msg": "Input should be 'ninguna', 'vegetariano', 'vegano', 'celiaco' or 'kosher'",
      "type": "enum"
    }
  ]
}
```

---

## Tablas DynamoDB

| Tabla | Clave primaria | Sort key | Descripción |
|---|---|---|---|
| `bistrotech-mesas` | `id_mesa` (N) | — | Una fila por mesa física |
| `bistrotech-reservas` | `id_reserva` (S) | — | Una fila por reserva (UUID) |
| `bistrotech-pedidos` | `id_pedido` (S) | — | Una fila por pedido (UUID) |
| `bistrotech-registros` | `id_mesa` (N) | `persona_ts` (S) = `{id_persona}#{ISO timestamp}` | Una fila por comensal por visita, con el output del ML y el feedback del POS |
| `bistrotech-clientes-historico` | `id_cliente` (N) | — | Perfil acumulado del cliente para imputación de ticket |
| `bistrotech-segmentos-referencia` | `segmento_pk` (S) = `{franja_etaria}#{franja_horaria}#{motivo}` | — | Medias por segmento para cold start |
