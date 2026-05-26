from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import mesas, reservas, pedidos

app = FastAPI(
    title="BistroTech API",
    description="Backend MLOps — recomendaciones de mozo y platos por mesa",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En prod: reemplazar con el dominio del front
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(mesas.router,    prefix="/api/v1/mesas",    tags=["mesas"])
app.include_router(reservas.router, prefix="/api/v1/reservas", tags=["reservas"])
app.include_router(pedidos.router,  prefix="/api/v1/pedidos",  tags=["pedidos"])


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "bistrotech-backend"}
