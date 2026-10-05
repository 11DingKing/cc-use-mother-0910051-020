from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.crud.vehicle import crud_vehicle, crud_production_batch
from app.schemas import (
    VehicleModel, VehicleModelCreate, VehicleModelUpdate,
    VehicleModelWithBOM, BOMItem, BOMItemCreate,
    ProductionBatch, ProductionBatchCreate, ProductionBatchUpdate,
    MaterialRequirement
)
from app.services.requirement import RequirementService

router = APIRouter(prefix="/vehicles", tags=["车型计划"])

@router.get("/", response_model=List[VehicleModel])
def read_vehicle_models(
    skip: int = 0,
    limit: int = 100,
    status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    if status == "active":
        return crud_vehicle.get_active_models(db)
    return crud_vehicle.get_multi(db, skip=skip, limit=limit)

@router.get("/{vehicle_id}", response_model=VehicleModelWithBOM)
def read_vehicle_model(vehicle_id: int, db: Session = Depends(get_db)):
    db_vehicle = crud_vehicle.get(db, vehicle_id)
    if db_vehicle is None:
        raise HTTPException(status_code=404, detail="车型不存在")
    return db_vehicle

@router.post("/", response_model=VehicleModel)
def create_vehicle_model(vehicle_in: VehicleModelCreate, db: Session = Depends(get_db)):
    existing = crud_vehicle.get_by_code(db, vehicle_in.code)
    if existing:
        raise HTTPException(status_code=400, detail="车型编码已存在")
    return crud_vehicle.create(db, obj_in=vehicle_in)

@router.put("/{vehicle_id}", response_model=VehicleModel)
def update_vehicle_model(
    vehicle_id: int,
    vehicle_in: VehicleModelUpdate,
    db: Session = Depends(get_db)
):
    db_vehicle = crud_vehicle.get(db, vehicle_id)
    if db_vehicle is None:
        raise HTTPException(status_code=404, detail="车型不存在")
    return crud_vehicle.update(db, db_obj=db_vehicle, obj_in=vehicle_in)

@router.post("/{vehicle_id}/bom", response_model=BOMItem)
def add_bom_item(
    vehicle_id: int,
    bom_item_in: BOMItemCreate,
    db: Session = Depends(get_db)
):
    db_vehicle = crud_vehicle.get(db, vehicle_id)
    if db_vehicle is None:
        raise HTTPException(status_code=404, detail="车型不存在")
    bom_item_in.vehicle_model_id = vehicle_id
    return crud_vehicle.add_bom_item(db, vehicle_model_id=vehicle_id, bom_item_in=bom_item_in)

@router.get("/{vehicle_id}/bom", response_model=List[BOMItem])
def get_bom_items(vehicle_id: int, db: Session = Depends(get_db)):
    return crud_vehicle.get_bom_items(db, vehicle_id)

@router.delete("/bom/{bom_item_id}")
def delete_bom_item(bom_item_id: int, db: Session = Depends(get_db)):
    crud_vehicle.remove_bom_item(db, bom_item_id)
    return {"message": "删除成功"}

@router.get("/{vehicle_id}/requirements", response_model=List[MaterialRequirement])
def get_vehicle_requirements(vehicle_id: int, db: Session = Depends(get_db)):
    return RequirementService.get_requirements_by_vehicle_model(db, vehicle_id)

@router.get("/batches/", response_model=List[ProductionBatch])
def read_production_batches(
    vehicle_id: Optional[int] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    if vehicle_id:
        return crud_production_batch.get_by_vehicle_model(db, vehicle_id)
    if status:
        return crud_production_batch.get_by_status(db, status)
    return crud_production_batch.get_multi(db)

@router.post("/batches/", response_model=ProductionBatch)
def create_production_batch(batch_in: ProductionBatchCreate, db: Session = Depends(get_db)):
    existing = crud_production_batch.get_by_batch_no(db, batch_in.batch_no)
    if existing:
        raise HTTPException(status_code=400, detail="批次号已存在")
    return crud_production_batch.create(db, obj_in=batch_in)

@router.put("/batches/{batch_id}", response_model=ProductionBatch)
def update_production_batch(
    batch_id: int,
    batch_in: ProductionBatchUpdate,
    db: Session = Depends(get_db)
):
    db_batch = crud_production_batch.get(db, batch_id)
    if db_batch is None:
        raise HTTPException(status_code=404, detail="生产批次不存在")
    return crud_production_batch.update(db, db_obj=db_batch, obj_in=batch_in)
