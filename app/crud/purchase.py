from sqlalchemy.orm import Session
from typing import List, Optional
from app.crud.base import CRUDBase
from app.models import PurchaseSuggestion, PurchaseOrder, Delivery, Inspection, InventoryBatch, DelayImpact
from app.schemas import (
    PurchaseSuggestionCreate, PurchaseOrderCreate, PurchaseOrderUpdate,
    DeliveryCreate, InspectionCreate, InventoryBatchCreate, InventoryBatchUpdate,
    DelayImpactCreate
)

class CRUDPurchaseSuggestion(CRUDBase[PurchaseSuggestion, PurchaseSuggestionCreate, dict]):
    def get_by_material(self, db: Session, material_id: int) -> List[PurchaseSuggestion]:
        return db.query(PurchaseSuggestion).filter(PurchaseSuggestion.material_id == material_id).all()

    def get_by_status(self, db: Session, status: str) -> List[PurchaseSuggestion]:
        return db.query(PurchaseSuggestion).filter(PurchaseSuggestion.status == status).all()

    def get_pending_suggestions(self, db: Session) -> List[PurchaseSuggestion]:
        return db.query(PurchaseSuggestion).filter(PurchaseSuggestion.status == "pending").all()

    def get_pending_by_material(self, db: Session, material_id: int) -> Optional[PurchaseSuggestion]:
        return db.query(PurchaseSuggestion).filter(
            PurchaseSuggestion.material_id == material_id,
            PurchaseSuggestion.status == "pending"
        ).first()

    def get_pending_quantity_by_material(self, db: Session, material_id: int) -> int:
        suggestions = db.query(PurchaseSuggestion).filter(
            PurchaseSuggestion.material_id == material_id,
            PurchaseSuggestion.status == "pending"
        ).all()
        return sum(s.suggested_quantity for s in suggestions)

    def delete_by_id(self, db: Session, id: int) -> int:
        obj = db.query(PurchaseSuggestion).filter(PurchaseSuggestion.id == id).first()
        if obj:
            db.delete(obj)
            db.commit()
            return 1
        return 0

crud_purchase_suggestion = CRUDPurchaseSuggestion(PurchaseSuggestion)

class CRUDPurchaseOrder(CRUDBase[PurchaseOrder, PurchaseOrderCreate, PurchaseOrderUpdate]):
    def get_by_order_no(self, db: Session, order_no: str) -> Optional[PurchaseOrder]:
        return db.query(PurchaseOrder).filter(PurchaseOrder.order_no == order_no).first()

    def get_by_supplier(self, db: Session, supplier_id: int) -> List[PurchaseOrder]:
        return db.query(PurchaseOrder).filter(PurchaseOrder.supplier_id == supplier_id).all()

    def get_by_material(self, db: Session, material_id: int) -> List[PurchaseOrder]:
        return db.query(PurchaseOrder).filter(PurchaseOrder.material_id == material_id).all()

    def get_by_status(self, db: Session, status: str) -> List[PurchaseOrder]:
        return db.query(PurchaseOrder).filter(PurchaseOrder.status == status).all()

    def get_delayed_orders(self, db: Session, today) -> List[PurchaseOrder]:
        return db.query(PurchaseOrder).filter(
            PurchaseOrder.expected_date < today,
            PurchaseOrder.status.in_(["ordered", "partial"])
        ).all()

    def get_in_transit_by_material(self, db: Session, material_id: int) -> List[PurchaseOrder]:
        return db.query(PurchaseOrder).filter(
            PurchaseOrder.material_id == material_id,
            PurchaseOrder.status.in_(["ordered", "partial"])
        ).all()

    def get_in_transit_quantity_by_material(self, db: Session, material_id: int) -> int:
        from app.models import Delivery
        orders = db.query(PurchaseOrder).filter(
            PurchaseOrder.material_id == material_id,
            PurchaseOrder.status.in_(["ordered", "partial"])
        ).all()
        total = 0
        for order in orders:
            delivered = db.query(Delivery).filter(
                Delivery.purchase_order_id == order.id
            ).all()
            delivered_qty = sum(d.quantity for d in delivered)
            total += max(0, order.quantity - delivered_qty)
        return total

    def get_in_transit_by_material_ordered_by_date(self, db: Session, material_id: int) -> List[dict]:
        from app.models import Delivery
        orders = db.query(PurchaseOrder).filter(
            PurchaseOrder.material_id == material_id,
            PurchaseOrder.status.in_(["ordered", "partial"])
        ).order_by(PurchaseOrder.expected_date).all()
        result = []
        for order in orders:
            delivered = db.query(Delivery).filter(
                Delivery.purchase_order_id == order.id
            ).all()
            delivered_qty = sum(d.quantity for d in delivered)
            remaining_qty = max(0, order.quantity - delivered_qty)
            if remaining_qty > 0:
                result.append({
                    "order_id": order.id,
                    "expected_date": order.expected_date,
                    "remaining_quantity": remaining_qty
                })
        return result

crud_purchase_order = CRUDPurchaseOrder(PurchaseOrder)

class CRUDDelivery(CRUDBase[Delivery, DeliveryCreate, dict]):
    def get_by_delivery_no(self, db: Session, delivery_no: str) -> Optional[Delivery]:
        return db.query(Delivery).filter(Delivery.delivery_no == delivery_no).first()

    def get_by_purchase_order(self, db: Session, purchase_order_id: int) -> List[Delivery]:
        return db.query(Delivery).filter(Delivery.purchase_order_id == purchase_order_id).all()

    def get_by_supplier(self, db: Session, supplier_id: int) -> List[Delivery]:
        return db.query(Delivery).filter(Delivery.supplier_id == supplier_id).all()

    def get_by_batch_no(self, db: Session, batch_no: str) -> List[Delivery]:
        return db.query(Delivery).filter(Delivery.batch_no == batch_no).all()

crud_delivery = CRUDDelivery(Delivery)

class CRUDInspection(CRUDBase[Inspection, InspectionCreate, dict]):
    def get_by_delivery(self, db: Session, delivery_id: int) -> Optional[Inspection]:
        return db.query(Inspection).filter(Inspection.delivery_id == delivery_id).first()

    def get_by_result(self, db: Session, result: str) -> List[Inspection]:
        return db.query(Inspection).filter(Inspection.result == result).all()

    def get_failed_inspections(self, db: Session) -> List[Inspection]:
        return db.query(Inspection).filter(Inspection.result != "passed").all()

crud_inspection = CRUDInspection(Inspection)

class CRUDInventoryBatch(CRUDBase[InventoryBatch, InventoryBatchCreate, InventoryBatchUpdate]):
    def get_by_material(self, db: Session, material_id: int) -> List[InventoryBatch]:
        return db.query(InventoryBatch).filter(
            InventoryBatch.material_id == material_id,
            InventoryBatch.is_quarantined == False
        ).all()

    def get_quarantined_batches(self, db: Session) -> List[InventoryBatch]:
        return db.query(InventoryBatch).filter(InventoryBatch.is_quarantined == True).all()

    def get_available_batches(self, db: Session, material_id: int) -> List[InventoryBatch]:
        return db.query(InventoryBatch).filter(
            InventoryBatch.material_id == material_id,
            InventoryBatch.is_quarantined == False,
            InventoryBatch.available_quantity > 0
        ).all()

    def get_total_stock(self, db: Session, material_id: int) -> int:
        batches = db.query(InventoryBatch).filter(
            InventoryBatch.material_id == material_id,
            InventoryBatch.is_quarantined == False
        ).all()
        return sum(batch.available_quantity for batch in batches)

crud_inventory_batch = CRUDInventoryBatch(InventoryBatch)

class CRUDDelayImpact(CRUDBase[DelayImpact, DelayImpactCreate, dict]):
    def get_by_purchase_order(self, db: Session, purchase_order_id: int) -> List[DelayImpact]:
        return db.query(DelayImpact).filter(DelayImpact.purchase_order_id == purchase_order_id).all()

    def get_by_production_batch(self, db: Session, production_batch_id: int) -> List[DelayImpact]:
        return db.query(DelayImpact).filter(DelayImpact.production_batch_id == production_batch_id).all()

    def delete_by_purchase_order(self, db: Session, purchase_order_id: int) -> int:
        deleted = db.query(DelayImpact).filter(DelayImpact.purchase_order_id == purchase_order_id).delete()
        db.commit()
        return deleted

crud_delay_impact = CRUDDelayImpact(DelayImpact)
