from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.crud.alternative import crud_alternative_material, crud_alternative_restriction
from app.crud.allocation import crud_alternative_ratio
from app.schemas import (
    AlternativeMaterial, AlternativeMaterialCreate,
    AlternativeMaterialRestriction, AlternativeMaterialRestrictionCreate,
    AlternativeCheckResult, AlternativeCheckRequest,
    AlternativeRatio, AlternativeRatioCreate, AlternativeRatioUpsert
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


@router.put("/{alt_id}/ratio", response_model=AlternativeRatio)
def upsert_alternative_ratio(
    alt_id: int,
    ratio_in: AlternativeRatioUpsert,
    db: Session = Depends(get_db)
):
    """配置替代换算比例（1单位替代料折合多少主料）与最大替代比例（0~1）"""
    alt = crud_alternative_material.get(db, alt_id)
    if not alt:
        raise HTTPException(status_code=404, detail="替代料关系不存在")
    if ratio_in.substitution_ratio is not None and ratio_in.substitution_ratio <= 0:
        raise HTTPException(status_code=400, detail="换算比例必须大于0")
    if ratio_in.max_share is not None and not (0 <= ratio_in.max_share <= 1):
        raise HTTPException(status_code=400, detail="最大替代比例必须在0~1之间")

    existing = crud_alternative_ratio.get_any_by_alternative(db, alt_id)
    if existing:
        update_data = ratio_in.model_dump(exclude_unset=True)
        for k, v in update_data.items():
            setattr(existing, k, v)
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return existing
    return crud_alternative_ratio.create(db, obj_in=AlternativeRatioCreate(
        alternative_id=alt_id,
        substitution_ratio=ratio_in.substitution_ratio
        if ratio_in.substitution_ratio is not None else 1.0,
        max_share=ratio_in.max_share,
        is_active=ratio_in.is_active if ratio_in.is_active is not None else True,
    ))


@router.get("/{alt_id}/ratio", response_model=AlternativeRatio)
def get_alternative_ratio(alt_id: int, db: Session = Depends(get_db)):
    ratio = crud_alternative_ratio.get_by_alternative(db, alt_id)
    if not ratio:
        raise HTTPException(status_code=404, detail="未配置替代比例，默认按1:1换算")
    return ratio
