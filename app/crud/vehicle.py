from sqlalchemy.orm import Session
from typing import List, Optional
from app.crud.base import CRUDBase
from app.models import VehicleModel, BOMItem, ProductionBatch
from app.schemas import VehicleModelCreate, VehicleModelUpdate, BOMItemCreate, ProductionBatchCreate, ProductionBatchUpdate

class CRUDVehicleModel(CRUDBase[VehicleModel, VehicleModelCreate, VehicleModelUpdate]):
    def get_by_code(self, db: Session, code: str) -> Optional[VehicleModel]:
        return db.query(VehicleModel).filter(VehicleModel.code == code).first()

    def get_active_models(self, db: Session) -> List[VehicleModel]:
        return db.query(VehicleModel).filter(VehicleModel.status == "active").all()

    def get_by_priority_range(self, db: Session, min_priority: int, max_priority: int) -> List[VehicleModel]:
        return db.query(VehicleModel).filter(
            VehicleModel.priority >= min_priority,
            VehicleModel.priority <= max_priority
        ).all()

    def add_bom_item(self, db: Session, *, vehicle_model_id: int, bom_item_in: BOMItemCreate) -> BOMItem:
        db_obj = BOMItem(**bom_item_in.model_dump())
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def get_bom_items(self, db: Session, vehicle_model_id: int) -> List[BOMItem]:
        return db.query(BOMItem).filter(BOMItem.vehicle_model_id == vehicle_model_id).all()

    def remove_bom_item(self, db: Session, bom_item_id: int) -> None:
        db_obj = db.query(BOMItem).filter(BOMItem.id == bom_item_id).first()
        if db_obj:
            db.delete(db_obj)
            db.commit()

crud_vehicle = CRUDVehicleModel(VehicleModel)

class CRUDProductionBatch(CRUDBase[ProductionBatch, ProductionBatchCreate, ProductionBatchUpdate]):
    def get_by_batch_no(self, db: Session, batch_no: str) -> Optional[ProductionBatch]:
        return db.query(ProductionBatch).filter(ProductionBatch.batch_no == batch_no).first()

    def get_by_vehicle_model(self, db: Session, vehicle_model_id: int) -> List[ProductionBatch]:
        return db.query(ProductionBatch).filter(ProductionBatch.vehicle_model_id == vehicle_model_id).all()

    def get_by_status(self, db: Session, status: str) -> List[ProductionBatch]:
        return db.query(ProductionBatch).filter(ProductionBatch.status == status).all()

    def get_planned_batches_after(self, db: Session, date) -> List[ProductionBatch]:
        return db.query(ProductionBatch).filter(
            ProductionBatch.plan_date >= date,
            ProductionBatch.status == "planned"
        ).all()

    def get_planned_batches_sorted(self, db: Session, after_date=None) -> List[ProductionBatch]:
        from app.models import VehicleModel
        query = db.query(ProductionBatch).filter(
            ProductionBatch.status == "planned"
        )
        if after_date:
            query = query.filter(ProductionBatch.plan_date >= after_date)
        return query.join(VehicleModel).order_by(
            ProductionBatch.plan_date,
            VehicleModel.priority.desc()
        ).all()

    def get_bom_quantity(self, db: Session, vehicle_model_id: int, material_id: int) -> Optional[int]:
        bom_item = db.query(BOMItem).filter(
            BOMItem.vehicle_model_id == vehicle_model_id,
            BOMItem.material_id == material_id
        ).first()
        return bom_item.quantity if bom_item else None

crud_production_batch = CRUDProductionBatch(ProductionBatch)
