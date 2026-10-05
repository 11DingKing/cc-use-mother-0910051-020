from sqlalchemy.orm import Session
from typing import List, Optional
from app.crud.base import CRUDBase
from app.models import Material
from app.schemas import MaterialCreate, MaterialUpdate

class CRUDMaterial(CRUDBase[Material, MaterialCreate, MaterialUpdate]):
    def get_by_code(self, db: Session, code: str) -> Optional[Material]:
        return db.query(Material).filter(Material.code == code).first()

    def get_by_category(self, db: Session, category: str) -> List[Material]:
        return db.query(Material).filter(Material.category == category).all()

    def get_critical_materials(self, db: Session) -> List[Material]:
        return db.query(Material).filter(Material.is_critical == True).all()

    def search(self, db: Session, keyword: str) -> List[Material]:
        return db.query(Material).filter(
            (Material.name.contains(keyword)) |
            (Material.code.contains(keyword)) |
            (Material.spec.contains(keyword))
        ).all()

crud_material = CRUDMaterial(Material)
