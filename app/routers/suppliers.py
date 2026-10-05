from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.crud.supplier import crud_supplier, crud_supply_capacity
from app.schemas import (
    Supplier, SupplierCreate, SupplierUpdate,
    SupplyCapacity, SupplyCapacityCreate, SupplyCapacityUpdate
)

router = APIRouter(prefix="/suppliers", tags=["供应商管理"])

@router.get("/", response_model=List[Supplier])
def read_suppliers(
    skip: int = 0,
    limit: int = 100,
    keyword: Optional[str] = None,
    min_rating: Optional[float] = None,
    db: Session = Depends(get_db)
):
    if keyword:
        return crud_supplier.search(db, keyword)
    if min_rating is not None:
        return crud_supplier.get_by_rating_above(db, min_rating)
    return crud_supplier.get_multi(db, skip=skip, limit=limit)

@router.get("/{supplier_id}", response_model=Supplier)
def read_supplier(supplier_id: int, db: Session = Depends(get_db)):
    db_supplier = crud_supplier.get(db, supplier_id)
    if db_supplier is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    return db_supplier

@router.post("/", response_model=Supplier)
def create_supplier(supplier_in: SupplierCreate, db: Session = Depends(get_db)):
    existing = crud_supplier.get_by_code(db, supplier_in.code)
    if existing:
        raise HTTPException(status_code=400, detail="供应商编码已存在")
    return crud_supplier.create(db, obj_in=supplier_in)

@router.put("/{supplier_id}", response_model=Supplier)
def update_supplier(
    supplier_id: int,
    supplier_in: SupplierUpdate,
    db: Session = Depends(get_db)
):
    db_supplier = crud_supplier.get(db, supplier_id)
    if db_supplier is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    return crud_supplier.update(db, db_obj=db_supplier, obj_in=supplier_in)

@router.get("/{supplier_id}/capacities", response_model=List[SupplyCapacity])
def get_supplier_capacities(supplier_id: int, db: Session = Depends(get_db)):
    return crud_supply_capacity.get_by_supplier(db, supplier_id)

@router.post("/capacities", response_model=SupplyCapacity)
def create_supply_capacity(capacity_in: SupplyCapacityCreate, db: Session = Depends(get_db)):
    existing = crud_supply_capacity.get_by_supplier_and_material(
        db, capacity_in.supplier_id, capacity_in.material_id
    )
    if existing:
        raise HTTPException(status_code=400, detail="该供应商物料供应能力已存在")
    return crud_supply_capacity.create(db, obj_in=capacity_in)

@router.get("/capacities/material/{material_id}", response_model=List[SupplyCapacity])
def get_material_capacities(material_id: int, preferred: bool = False, db: Session = Depends(get_db)):
    if preferred:
        return crud_supply_capacity.get_preferred_suppliers(db, material_id)
    return crud_supply_capacity.get_by_material(db, material_id)

@router.put("/capacities/{capacity_id}", response_model=SupplyCapacity)
def update_supply_capacity(
    capacity_id: int,
    capacity_in: SupplyCapacityUpdate,
    db: Session = Depends(get_db)
):
    db_capacity = crud_supply_capacity.get(db, capacity_id)
    if db_capacity is None:
        raise HTTPException(status_code=404, detail="供应能力不存在")
    return crud_supply_capacity.update(db, db_obj=db_capacity, obj_in=capacity_in)
