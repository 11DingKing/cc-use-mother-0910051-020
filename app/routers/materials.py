from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.crud.material import crud_material
from app.schemas import Material, MaterialCreate, MaterialUpdate

router = APIRouter(prefix="/materials", tags=["物料管理"])

@router.get("/", response_model=List[Material])
def read_materials(
    skip: int = 0,
    limit: int = 100,
    category: Optional[str] = None,
    keyword: Optional[str] = None,
    db: Session = Depends(get_db)
):
    if keyword:
        return crud_material.search(db, keyword)
    if category:
        return crud_material.get_by_category(db, category)
    return crud_material.get_multi(db, skip=skip, limit=limit)

@router.get("/critical", response_model=List[Material])
def read_critical_materials(db: Session = Depends(get_db)):
    return crud_material.get_critical_materials(db)

@router.get("/{material_id}", response_model=Material)
def read_material(material_id: int, db: Session = Depends(get_db)):
    db_material = crud_material.get(db, material_id)
    if db_material is None:
        raise HTTPException(status_code=404, detail="物料不存在")
    return db_material

@router.post("/", response_model=Material)
def create_material(material_in: MaterialCreate, db: Session = Depends(get_db)):
    existing = crud_material.get_by_code(db, material_in.code)
    if existing:
        raise HTTPException(status_code=400, detail="物料编码已存在")
    return crud_material.create(db, obj_in=material_in)

@router.put("/{material_id}", response_model=Material)
def update_material(
    material_id: int,
    material_in: MaterialUpdate,
    db: Session = Depends(get_db)
):
    db_material = crud_material.get(db, material_id)
    if db_material is None:
        raise HTTPException(status_code=404, detail="物料不存在")
    return crud_material.update(db, db_obj=db_material, obj_in=material_in)

@router.delete("/{material_id}")
def delete_material(material_id: int, db: Session = Depends(get_db)):
    db_material = crud_material.get(db, material_id)
    if db_material is None:
        raise HTTPException(status_code=404, detail="物料不存在")
    crud_material.remove(db, id=material_id)
    return {"message": "删除成功"}
