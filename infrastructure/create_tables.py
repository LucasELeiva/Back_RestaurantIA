"""
Crea las tres tablas DynamoDB de BistroTech.

Uso:
  python infrastructure/create_tables.py                  # AWS real (us-east-1)
  python infrastructure/create_tables.py --local          # DynamoDB Local en localhost:8000

Las tablas ya existentes se omiten sin error.
"""

import argparse
import boto3
from botocore.exceptions import ClientError

TABLES = [
    {
        "TableName": "bistrotech-mesas",
        "KeySchema": [
            {"AttributeName": "id_mesa", "KeyType": "HASH"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "id_mesa", "AttributeType": "N"},
        ],
        "BillingMode": "PAY_PER_REQUEST",
        "comment": "Una fila por mesa física. Estado: libre/ocupada/reservada. Soft delete con activa=False.",
    },
    {
        "TableName": "bistrotech-reservas",
        "KeySchema": [
            {"AttributeName": "id_reserva", "KeyType": "HASH"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "id_reserva", "AttributeType": "S"},
        ],
        "BillingMode": "PAY_PER_REQUEST",
        "comment": "Una fila por reserva. id_reserva es UUID. Estado: confirmada/cancelada/completada.",
    },
    {
        "TableName": "bistrotech-registros",
        "KeySchema": [
            {"AttributeName": "id_mesa",    "KeyType": "HASH"},
            {"AttributeName": "persona_ts", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "id_mesa",    "AttributeType": "N"},
            {"AttributeName": "persona_ts", "AttributeType": "S"},
        ],
        "BillingMode": "PAY_PER_REQUEST",
        "comment": (
            "Una fila por comensal por visita. "
            "persona_ts = '{id_persona_en_mesa}#{ISO-timestamp}'. "
            "Sin columnas de feedback — esas se actualizan desde el POS post-servicio."
        ),
    },
    {
        "TableName": "bistrotech-clientes-historico",
        "KeySchema": [
            {"AttributeName": "id_cliente", "KeyType": "HASH"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "id_cliente", "AttributeType": "N"},
        ],
        "BillingMode": "PAY_PER_REQUEST",
        "comment": (
            "Perfil acumulado por cliente identificado. "
            "Campos: visitas_totales, ticket_promedio, restriccion_detectada, "
            "motivo_frecuente, franja_horaria_frecuente, like_rate_promedio, platos_frecuentes."
        ),
    },
    {
        "TableName": "bistrotech-segmentos-referencia",
        "KeySchema": [
            {"AttributeName": "segmento_pk", "KeyType": "HASH"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "segmento_pk", "AttributeType": "S"},
        ],
        "BillingMode": "PAY_PER_REQUEST",
        "comment": (
            "Medias por segmento para cold start. "
            "segmento_pk = '{franja_etaria}#{franja_horaria}#{motivo_visita}'. "
            "Campos: ticket_promedio_segmento, platos_populares_segmento, propina_rate_segmento."
        ),
    },
]


def create_tables(endpoint_url: str | None = None):
    kwargs = {"region_name": "us-east-1"}
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url

    dynamodb = boto3.client("dynamodb", **kwargs)

    for table_def in TABLES:
        name = table_def["TableName"]
        params = {k: v for k, v in table_def.items() if k != "comment"}
        try:
            dynamodb.create_table(**params)
            print(f"[OK] Tabla creada: {name}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceInUseException":
                print(f"[--] Tabla ya existe, omitiendo: {name}")
            else:
                raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true",
                        help="Conectar a DynamoDB Local en http://localhost:8000")
    args = parser.parse_args()

    endpoint = "http://localhost:8000" if args.local else None
    create_tables(endpoint_url=endpoint)
