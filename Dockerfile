FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Variables de entorno — sobreescribir en ECS Task Definition o Lambda env
ENV ML_BACKEND=sagemaker
ENV SAGEMAKER_ENDPOINT=bistrotech-endpoint
ENV AWS_REGION=us-east-1
ENV MODELO_VERSION=v1.0

ENV DYNAMODB_TABLE_REGISTROS=bistrotech-registros
ENV DYNAMODB_TABLE_CLIENTES=bistrotech-clientes-historico
ENV DYNAMODB_TABLE_SEGMENTOS=bistrotech-segmentos-referencia
ENV DYNAMODB_TABLE_MESAS=bistrotech-mesas
ENV DYNAMODB_TABLE_RESERVAS=bistrotech-reservas
ENV DYNAMODB_TABLE_PEDIDOS=bistrotech-pedidos
# DYNAMODB_ENDPOINT_URL — dejar vacío en prod; setear a http://localhost:8000 para DynamoDB Local

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
