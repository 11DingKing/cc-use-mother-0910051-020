from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import date
from app.database import get_db
from app.crud.purchase import (
    crud_purchase_suggestion, crud_purchase_order,
    crud_delivery, crud_inspection, crud_inventory_batch
)
from app.schemas import (
    PurchaseSuggestion, PurchaseSuggestionCreate,
    PurchaseOrder, PurchaseOrderCreate, PurchaseOrderUpdate,
    Delivery, DeliveryCreate,
    Inspection, InspectionCreate,
    InventoryBatch, InventoryBatchUpdate,
    MaterialRequirement, PurchaseSuggestionGenerateRequest,
    DelayImpactAnalysisRequest, DelayImpactAnalysisResult
)
from app.services.requirement import RequirementService
from app.services.purchase import PurchaseService
from app.services.delay_analysis import DelayAnalysisService
from app.services.inspection import InspectionService

router = APIRouter(prefix="/purchase", tags=["采购管理"])

@router.get("/requirements", response_model=List[MaterialRequirement])
def calculate_requirements(
    include_safety_stock: bool = True,
    priorities: Optional[str] = Query(None, description="优先级列表，逗号分隔"),
    db: Session = Depends(get_db)
):
    priority_list = None
    if priorities:
        priority_list = [int(p) for p in priorities.split(",")]
    return RequirementService.calculate_material_requirements(
        db, priority_list, include_safety_stock
    )

@router.get("/suggestions", response_model=List[PurchaseSuggestion])
def get_purchase_suggestions(
    status: Optional[str] = None,
    material_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    if status:
        return crud_purchase_suggestion.get_by_status(db, status)
    if material_id:
        return crud_purchase_suggestion.get_by_material(db, material_id)
    return crud_purchase_suggestion.get_multi(db)

@router.post("/suggestions/generate", response_model=List[PurchaseSuggestion])
def generate_suggestions(
    request: PurchaseSuggestionGenerateRequest,
    db: Session = Depends(get_db)
):
    return PurchaseService.generate_purchase_suggestions(
        db, request.vehicle_model_priorities, request.include_safety_stock
    )

@router.post("/suggestions/{suggestion_id}/convert", response_model=PurchaseOrder)
def convert_suggestion_to_order(
    suggestion_id: int,
    order_no: str,
    supplier_id: Optional[int] = None,
    quantity: Optional[int] = None,
    expected_date: Optional[date] = None,
    db: Session = Depends(get_db)
):
    try:
        return PurchaseService.convert_suggestion_to_order(
            db, suggestion_id, order_no, supplier_id, quantity, expected_date
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/orders", response_model=List[PurchaseOrder])
def get_purchase_orders(
    status: Optional[str] = None,
    supplier_id: Optional[int] = None,
    material_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    if status:
        return crud_purchase_order.get_by_status(db, status)
    if supplier_id:
        return crud_purchase_order.get_by_supplier(db, supplier_id)
    if material_id:
        return crud_purchase_order.get_by_material(db, material_id)
    return crud_purchase_order.get_multi(db)

@router.post("/orders", response_model=PurchaseOrder)
def create_purchase_order(order_in: PurchaseOrderCreate, db: Session = Depends(get_db)):
    existing = crud_purchase_order.get_by_order_no(db, order_in.order_no)
    if existing:
        raise HTTPException(status_code=400, detail="订单号已存在")
    return crud_purchase_order.create(db, obj_in=order_in)

@router.put("/orders/{order_id}", response_model=PurchaseOrder)
def update_purchase_order(
    order_id: int,
    order_in: PurchaseOrderUpdate,
    db: Session = Depends(get_db)
):
    db_order = crud_purchase_order.get(db, order_id)
    if db_order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    return crud_purchase_order.update(db, db_obj=db_order, obj_in=order_in)

@router.post("/orders/analyze-delay", response_model=DelayImpactAnalysisResult)
def analyze_delay_impact(
    request: DelayImpactAnalysisRequest,
    db: Session = Depends(get_db)
):
    try:
        return DelayAnalysisService.analyze_delay_impact(
            db, request.purchase_order_id, request.new_expected_date, request.delay_days
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/deliveries", response_model=List[Delivery])
def get_deliveries(
    supplier_id: Optional[int] = None,
    purchase_order_id: Optional[int] = None,
    batch_no: Optional[str] = None,
    db: Session = Depends(get_db)
):
    if supplier_id:
        return crud_delivery.get_by_supplier(db, supplier_id)
    if purchase_order_id:
        return crud_delivery.get_by_purchase_order(db, purchase_order_id)
    if batch_no:
        return crud_delivery.get_by_batch_no(db, batch_no)
    return crud_delivery.get_multi(db)

@router.post("/deliveries", response_model=Delivery)
def create_delivery(delivery_in: DeliveryCreate, db: Session = Depends(get_db)):
    existing = crud_delivery.get_by_delivery_no(db, delivery_in.delivery_no)
    if existing:
        raise HTTPException(status_code=400, detail="送货单号已存在")
    return crud_delivery.create(db, obj_in=delivery_in)

@router.post("/inspections", response_model=Inspection)
def create_inspection(inspection_in: InspectionCreate, db: Session = Depends(get_db)):
    try:
        return InspectionService.record_inspection_and_process(db, inspection_in)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/inspections", response_model=List[Inspection])
def get_inspections(
    result: Optional[str] = None,
    failed_only: bool = False,
    db: Session = Depends(get_db)
):
    if failed_only:
        return crud_inspection.get_failed_inspections(db)
    if result:
        return crud_inspection.get_by_result(db, result)
    return crud_inspection.get_multi(db)

@router.get("/inventory", response_model=List[InventoryBatch])
def get_inventory_batches(
    material_id: Optional[int] = None,
    quarantined_only: bool = False,
    available_only: bool = False,
    db: Session = Depends(get_db)
):
    if quarantined_only:
        return crud_inventory_batch.get_quarantined_batches(db)
    if available_only and material_id:
        return crud_inventory_batch.get_available_batches(db, material_id)
    if material_id:
        return crud_inventory_batch.get_by_material(db, material_id)
    return crud_inventory_batch.get_multi(db)

@router.put("/inventory/{batch_id}/release")
def release_quarantined_batch(
    batch_id: int,
    reason: str,
    db: Session = Depends(get_db)
):
    try:
        batch = InspectionService.release_quarantined_batch(db, batch_id, reason)
        return {"message": "已解除隔离", "batch": batch}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/inventory/{batch_id}/reject")
def reject_quarantined_batch(
    batch_id: int,
    reason: str,
    db: Session = Depends(get_db)
):
    try:
        batch = InspectionService.reject_quarantined_batch(db, batch_id, reason)
        return {"message": "已拒收", "batch": batch}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/inventory/{batch_id}", response_model=InventoryBatch)
def update_inventory_batch(
    batch_id: int,
    batch_in: InventoryBatchUpdate,
    db: Session = Depends(get_db)
):
    db_batch = crud_inventory_batch.get(db, batch_id)
    if db_batch is None:
        raise HTTPException(status_code=404, detail="库存批次不存在")
    return crud_inventory_batch.update(db, db_obj=db_batch, obj_in=batch_in)

@router.post("/consume")
def consume_material(
    material_id: int,
    quantity: int,
    db: Session = Depends(get_db)
):
    success = InspectionService.consume_material(db, material_id, quantity)
    if not success:
        raise HTTPException(status_code=400, detail="库存不足")
    return {"message": "领料成功", "consumed_quantity": quantity}
