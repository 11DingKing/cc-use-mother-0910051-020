from sqlalchemy.orm import Session
from typing import List, Optional
from app.crud.alternative import crud_alternative_material, crud_alternative_restriction
from app.crud.material import crud_material
from app.crud.purchase import crud_inventory_batch
from app.schemas import AlternativeCheckResult, AlternativeCheckRequest
from app.models import AlternativeMaterial

class AlternativeMaterialService:
    @staticmethod
    def check_alternative_availability(
        db: Session,
        material_id: int,
        vehicle_model_id: int,
        required_quantity: int
    ) -> AlternativeCheckResult:
        original_material = crud_material.get(db, material_id)
        if not original_material:
            raise ValueError(f"物料不存在: {material_id}")
        alternatives = crud_alternative_material.get_alternatives_for_material(db, material_id)
        available_alternatives: List[AlternativeMaterial] = []
        total_available_qty = 0
        recommended = None
        for alt in alternatives:
            if not crud_alternative_restriction.is_alternative_allowed(
                db, alt.id, vehicle_model_id
            ):
                continue
            alt_material_id = alt.alternative_material_id
            stock_qty = crud_inventory_batch.get_total_stock(db, alt_material_id)
            if stock_qty > 0:
                available_alternatives.append(alt)
                total_available_qty += stock_qty
                if recommended is None and stock_qty >= required_quantity:
                    recommended = alt
        if not recommended and available_alternatives:
            available_alternatives.sort(key=lambda a: (-a.priority,))
            recommended = available_alternatives[0]
        can_be_replaced = total_available_qty >= required_quantity and len(available_alternatives) > 0
        return AlternativeCheckResult(
            original_material=original_material,
            available_alternatives=available_alternatives,
            can_be_replaced=can_be_replaced,
            recommended_alternative=recommended,
            total_available_quantity=total_available_qty
        )

    @staticmethod
    def get_allowed_alternatives_for_vehicle(
        db: Session,
        material_id: int,
        vehicle_model_id: int
    ) -> List[AlternativeMaterial]:
        alternatives = crud_alternative_material.get_alternatives_for_material(db, material_id)
        allowed = []
        for alt in alternatives:
            if crud_alternative_restriction.is_alternative_allowed(db, alt.id, vehicle_model_id):
                allowed.append(alt)
        return allowed

    @staticmethod
    def can_use_alternative(
        db: Session,
        material_id: int,
        alternative_material_id: int,
        vehicle_model_id: int
    ) -> bool:
        alt = crud_alternative_material.get_by_materials(db, material_id, alternative_material_id)
        if not alt or not alt.is_active:
            return False
        return crud_alternative_restriction.is_alternative_allowed(db, alt.id, vehicle_model_id)
