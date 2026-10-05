from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.crud.alternative import crud_alternative_material, crud_alternative_restriction
from app.schemas import (
    AlternativeMaterial, AlternativeMaterialCreate,
    AlternativeMaterialRestriction, AlternativeMaterialRestrictionCreate,
    AlternativeCheckResult, AlternativeCheckRequest
)
from app.services.alternative_material import AlternativeMaterialService

router = APIRouter(prefix="/alternatives", tags=["替代料管理"])

@router.get("/material/{material_id}", response_model=List[AlternativeMaterial])
def get_alternatives_for_material(material_id: int, db: Session = Depends(get_db)):
    return crud_alternative_material.get_alternatives_for_material(db, material_id)

@router.post("/", response_model=AlternativeMaterial)
def create_alternative_material(
    alt_in: AlternativeMaterialCreate,
    db: Session = Depends(get_db)
):
    existing = crud_alternative_material.get_by_materials(
        db, alt_in.material_id, alt_in.alternative_material_id
    )
    if existing:
        raise HTTPException(status_code=400, detail="替代料关系已存在")
    if alt_in.material_id == alt_in.alternative_material_id:
        raise HTTPException(status_code=400, detail="不能替代自身")
    return crud_alternative_material.create(db, obj_in=alt_in)

@router.put("/{alt_id}/deactivate", response_model=AlternativeMaterial)
def deactivate_alternative(alt_id: int, db: Session = Depends(get_db)):
    db_alt = crud_alternative_material.deactivate(db, alt_id)
    if db_alt is None:
        raise HTTPException(status_code=404, detail="替代料关系不存在")
    return db_alt

@router.get("/vehicle/{material_id}/{vehicle_id}", response_model=List[AlternativeMaterial])
def get_allowed_alternatives_for_vehicle(
    material_id: int,
    vehicle_id: int,
    db: Session = Depends(get_db)
):
    return AlternativeMaterialService.get_allowed_alternatives_for_vehicle(
        db, material_id, vehicle_id
    )

@router.post("/check", response_model=AlternativeCheckResult)
def check_alternative_availability(
    request: AlternativeCheckRequest,
    db: Session = Depends(get_db)
):
    try:
        return AlternativeMaterialService.check_alternative_availability(
            db, request.material_id, request.vehicle_model_id, request.required_quantity
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/can-use")
def can_use_alternative(
    material_id: int,
    alternative_material_id: int,
    vehicle_model_id: int,
    db: Session = Depends(get_db)
):
    allowed = AlternativeMaterialService.can_use_alternative(
        db, material_id, alternative_material_id, vehicle_model_id
    )
    return {"allowed": allowed}

@router.post("/restrictions", response_model=AlternativeMaterialRestriction)
def create_alternative_restriction(
    restriction_in: AlternativeMaterialRestrictionCreate,
    db: Session = Depends(get_db)
):
    existing = crud_alternative_restriction.get_by_alternative_and_vehicle(
        db, restriction_in.alternative_id, restriction_in.vehicle_model_id
    )
    if existing:
        raise HTTPException(status_code=400, detail="该限制已存在")
    return crud_alternative_restriction.create(db, obj_in=restriction_in)

@router.get("/restrictions/alternative/{alt_id}", response_model=List[AlternativeMaterialRestriction])
def get_restrictions_for_alternative(alt_id: int, db: Session = Depends(get_db)):
    return crud_alternative_restriction.get_by_alternative(db, alt_id)
