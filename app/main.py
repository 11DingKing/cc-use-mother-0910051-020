from fastapi import FastAPI
from sqlalchemy import inspect, text
from app.config import settings
from app.database import engine, Base, get_db
from app.routers import materials, vehicles, suppliers, purchase, alternatives, statistics
from app.routers import supplier_confirmations, allocations
from app.data.seed import seed_all

Base.metadata.create_all(bind=engine)


def _run_lightweight_migrations():
    """为已有数据库补充新增列（create_all 不会修改已存在的表）"""
    inspector = inspect(engine)
    if "alternative_materials" in inspector.get_table_names():
        columns = {c["name"] for c in inspector.get_columns("alternative_materials")}
        with engine.begin() as conn:
            if "substitution_ratio" not in columns:
                conn.execute(text(
                    "ALTER TABLE alternative_materials "
                    "ADD COLUMN substitution_ratio FLOAT DEFAULT 1.0"
                ))
            if "max_substitution_percent" not in columns:
                conn.execute(text(
                    "ALTER TABLE alternative_materials "
                    "ADD COLUMN max_substitution_percent INTEGER DEFAULT 100"
                ))


_run_lightweight_migrations()

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
app.include_router(allocations.router, prefix=settings.API_V1_STR)

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
