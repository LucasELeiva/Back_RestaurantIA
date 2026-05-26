# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Instalar dependencias
pip install -r requirements.txt

# Run the API server
python -m uvicorn app.main:app --reload

# Run all tests
python -m pytest test_predict.py -v

# Run a single test
python -m pytest test_predict.py::test_crear_pedido_happy_path -v

# Create DynamoDB tables (AWS)
python infrastructure/create_tables.py

# Create DynamoDB tables (local)
python infrastructure/create_tables.py --local

# Build Docker image
docker build -t bistrotech .
```

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `ML_BACKEND` | `local` | `"local"` uses deterministic fallback; `"sagemaker"` calls AWS |
| `SAGEMAKER_ENDPOINT` | `bistrotech-endpoint` | SageMaker endpoint name |
| `AWS_REGION` | `us-east-1` | AWS region |
| `MODELO_VERSION` | `v1.0` | Version tag returned in responses |
| `DYNAMODB_ENDPOINT_URL` | _(not set)_ | Set to `http://localhost:8000` for DynamoDB Local |
| `DYNAMODB_TABLE_REGISTROS` | `bistrotech-registros` | Per-visit records table |
| `DYNAMODB_TABLE_CLIENTES` | `bistrotech-clientes-historico` | Customer history table |
| `DYNAMODB_TABLE_SEGMENTOS` | `bistrotech-segmentos-referencia` | Segment averages for cold start |
| `DYNAMODB_TABLE_MESAS` | `bistrotech-mesas` | Tables/mesas table |
| `DYNAMODB_TABLE_RESERVAS` | `bistrotech-reservas` | Reservations table |
| `DYNAMODB_TABLE_PEDIDOS` | `bistrotech-pedidos` | Orders table |

Tests run with `ML_BACKEND=local` by default (no AWS credentials needed). DynamoDB calls silently fail/warn when no local instance is running — tests still pass because they only assert on the API response structure.

## Architecture

### Request flow

```
POST /api/v1/mesas/{id_mesa}/pedidos
  → mesas.router.crear_pedido
    → pedido_service.create_pedido
      → ml_client.run_inference       ← builds feature payload, calls SageMaker or fallback
      → dynamo_client.save_registro   ← writes one row per comensal to bistrotech-registros
      → DynamoDB bistrotech-pedidos   ← stores the full pedido with inference results as JSON
```

Feedback from the POS flows back:
```
POST /api/v1/pedidos/{id_pedido}/feedback
  → pedido_service.submit_feedback
    → dynamo_client.update_feedback_by_key  ← updates each comensal row in bistrotech-registros
    → marks pedido estado="cerrado"
```

### ML inference (`app/services/ml_client.py`)

Two backends controlled by `ML_BACKEND`:
- **`local`** — `_fallback_local()` returns a deterministic response (8 mozos, top-3 platos per course per comensal). Used for development without AWS.
- **`sagemaker`** — `_call_sagemaker()` sends the payload to the endpoint. Falls back to `_fallback_local()` on any AWS error.

Before calling either backend, `_build_payload()` does feature engineering:
- `dia_semana` → cyclical encoding (`sin`/`cos`) so Mon/Sun are neighbors
- `visitas_previas` → `log1p` transform
- `ticket_promedio_historico=None` → imputed via DynamoDB: first tries `bistrotech-clientes-historico` (by `id_cliente`), then `bistrotech-segmentos-referencia` (by `franja_etaria#franja_horaria#motivo_visita`), then passes `None` to the model

### DynamoDB tables

| Table | PK | SK | Purpose |
|---|---|---|---|
| `bistrotech-registros` | `id_mesa` (N) | `persona_ts` (S) = `{id_persona}#{ISO-ts}` | One row per comensal per visit; feedback fields added post-service |
| `bistrotech-pedidos` | `id_pedido` (S) | — | Full pedido; ML outputs stored as JSON strings |
| `bistrotech-mesas` | `id_mesa` (N) | — | Physical tables; soft-delete via `activa=False` |
| `bistrotech-reservas` | `id_reserva` (S) | — | UUID-keyed reservations |
| `bistrotech-clientes-historico` | `id_cliente` (N) | — | Accumulated customer profile for imputation |
| `bistrotech-segmentos-referencia` | `segmento_pk` (S) | — | Segment averages for cold-start imputation |

### Domain model

- **Mesa** — physical restaurant table (capacidad, ubicacion: salon/privado, estado: libre/ocupada/reservada)
- **Comensal** — one person at the table; `id_cliente` is nullable (walk-ins). `es_repetidor` and `visitas_previas` are cross-validated (a schema-level validator rejects incoherent combinations).
- **Pedido** — ties a mesa + comensales + ML output together. Created when the table orders; closed when POS sends feedback.
- **Plato IDs** — fixed ranges: entradas 1–8, principales 9–20, postres 21–25, bebidas 26–30, mozos 1–8.

### Router layout

`app/routers/predict.py` exposes a legacy `POST /predict` + `POST /feedback` (not mounted in `main.py` — appears unused). The active flow goes through `mesas.router` and `pedidos.router`.
