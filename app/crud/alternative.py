from sqlalchemy.orm import Session
from typing import List, Optional
from app.crud.base import CRUDBase
from app.models import AlternativeMaterial, AlternativeMaterialRestriction
from app.schemas import AlternativeMaterialCreate, AlternativeMaterialRestrictionCreate

class CRUDAlternativeMaterial(CRUDBase[AlternativeMaterial, AlternativeMaterialCreate, dict]):
    def get_alternatives_for_material(self, db: Session, material_id: int) -> List[AlternativeMaterial]:
        return db.query(AlternativeMaterial).filter(
            AlternativeMaterial.material_id == material_id,
            AlternativeMaterial.is_active == True
        ).order_by(AlternativeMaterial.priority).all()

    def get_alternative_for(self, db: Session, alternative_material_id: int) -> List[AlternativeMaterial]:
        return db.query(AlternativeMaterial).filter(
            AlternativeMaterial.alternative_material_id == alternative_material_id,
            AlternativeMaterial.is_active == True
        ).all()

    def get_by_materials(self, db: Session, material_id: int, alternative_material_id: int) -> Optional[AlternativeMaterial]:
        return db.query(AlternativeMaterial).filter(
            AlternativeMaterial.material_id == material_id,
            AlternativeMaterial.alternative_material_id == alternative_material_id
        ).first()

    def deactivate(self, db: Session, id: int) -> Optional[AlternativeMaterial]:
        db_obj = self.get(db, id)
        if db_obj:
            db_obj.is_active = False
            db.commit()
            db.refresh(db_obj)
        return db_obj

crud_alternative_material = CRUDAlternativeMaterial(AlternativeMaterial)

class CRUDAlternativeMaterialRestriction(CRUDBase[AlternativeMaterialRestriction, AlternativeMaterialRestrictionCreate, dict]):
    def get_by_alternative(self, db: Session, alternative_id: int) -> List[AlternativeMaterialRestriction]:
        return db.query(AlternativeMaterialRestriction).filter(
            AlternativeMaterialRestriction.alternative_id == alternative_id
        ).all()

    def get_by_alternative_and_vehicle(self, db: Session, alternative_id: int, vehicle_model_id: int) -> Optional[AlternativeMaterialRestriction]:
        return db.query(AlternativeMaterialRestriction).filter(
            AlternativeMaterialRestriction.alternative_id == alternative_id,
            AlternativeMaterialRestriction.vehicle_model_id == vehicle_model_id
        ).first()

    def is_alternative_allowed(self, db: Session, alternative_id: int, vehicle_model_id: int) -> bool:
        restriction = self.get_by_alternative_and_vehicle(db, alternative_id, vehicle_model_id)
        if restriction:
            return restriction.is_allowed
        return True

crud_alternative_restriction = CRUDAlternativeMaterialRestriction(AlternativeMaterialRestriction)
