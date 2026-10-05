from fastapi import FastAPI
from app.config import settings
from app.database import engine, Base, get_db
from app.routers import materials, vehicles, suppliers, purchase, alternatives, statistics
from app.routers import supplier_confirmations
from app.data.seed import seed_all

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="国产自行车零部件供应协同系统 - 从一颗滚珠到整套飞轮，零部件供应协同平台",
    version="1.0.0"
)

app.include_router(materials.router, prefix=settings.API_V1_STR)
app.include_router(vehicles.router, prefix=settings.API_V1_STR)
app.include_router(suppliers.router, prefix=settings.API_V1_STR)
app.include_router(purchase.router, prefix=settings.API_V1_STR)
app.include_router(alternatives.router, prefix=settings.API_V1_STR)
app.include_router(statistics.router, prefix=settings.API_V1_STR)
app.include_router(supplier_confirmations.router, prefix=settings.API_V1_STR)

@app.on_event("startup")
def startup_event():
    db = next(get_db())
    try:
        seed_all(db)
    finally:
        db.close()

@app.get("/")
def root():
    return {
        "message": "欢迎使用国产自行车零部件供应协同系统",
        "version": "1.0.0",
        "docs_url": "/docs",
        "api_prefix": settings.API_V1_STR
    }

@app.get("/health")
def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
