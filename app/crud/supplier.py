from sqlalchemy.orm import Session
from typing import List, Optional
from app.crud.base import CRUDBase
from app.models import Supplier, SupplyCapacity
from app.schemas import SupplierCreate, SupplierUpdate, SupplyCapacityCreate, SupplyCapacityUpdate

class CRUDSupplier(CRUDBase[Supplier, SupplierCreate, SupplierUpdate]):
    def get_by_code(self, db: Session, code: str) -> Optional[Supplier]:
        return db.query(Supplier).filter(Supplier.code == code).first()

    def search(self, db: Session, keyword: str) -> List[Supplier]:
        return db.query(Supplier).filter(
            (Supplier.name.contains(keyword)) |
            (Supplier.code.contains(keyword))
        ).all()

    def get_by_rating_above(self, db: Session, min_rating: float) -> List[Supplier]:
        return db.query(Supplier).filter(Supplier.rating >= min_rating).all()

crud_supplier = CRUDSupplier(Supplier)

class CRUDSupplyCapacity(CRUDBase[SupplyCapacity, SupplyCapacityCreate, SupplyCapacityUpdate]):
    def get_by_supplier_and_material(self, db: Session, supplier_id: int, material_id: int) -> Optional[SupplyCapacity]:
        return db.query(SupplyCapacity).filter(
            SupplyCapacity.supplier_id == supplier_id,
            SupplyCapacity.material_id == material_id
        ).first()

    def get_by_material(self, db: Session, material_id: int) -> List[SupplyCapacity]:
        return db.query(SupplyCapacity).filter(SupplyCapacity.material_id == material_id).all()

    def get_by_supplier(self, db: Session, supplier_id: int) -> List[SupplyCapacity]:
        return db.query(SupplyCapacity).filter(SupplyCapacity.supplier_id == supplier_id).all()

    def get_preferred_suppliers(self, db: Session, material_id: int) -> List[SupplyCapacity]:
        return db.query(SupplyCapacity).filter(
            SupplyCapacity.material_id == material_id,
            SupplyCapacity.is_preferred == True
        ).all()

crud_supply_capacity = CRUDSupplyCapacity(SupplyCapacity)
