from sqlalchemy.orm import Session
from typing import List, Optional, Tuple
from datetime import date, timedelta
from app.crud.purchase import crud_purchase_suggestion, crud_purchase_order
from app.crud.supplier import crud_supply_capacity
from app.crud.material import crud_material
from app.services.requirement import RequirementService
from app.schemas import (
    PurchaseSuggestionCreate, MaterialRequirement, PurchaseSuggestion
)
from app.models import PurchaseOrder, SupplyCapacity

class PurchaseService:
    @staticmethod
    def generate_purchase_suggestions(
        db: Session,
        vehicle_model_priorities: Optional[List[int]] = None,
        include_safety_stock: bool = True
    ) -> List[PurchaseSuggestion]:
        existing_pending = crud_purchase_suggestion.get_pending_suggestions(db)
        pending_map = {s.material_id: s for s in existing_pending}
        requirements = RequirementService.calculate_material_requirements(
            db, vehicle_model_priorities, include_safety_stock
        )
        result_suggestions: List[PurchaseSuggestion] = []
        processed_material_ids = set()

        for req in requirements:
            processed_material_ids.add(req.material_id)
            purchase_quantity = req.gross_shortage if req.gross_shortage > 0 else req.shortage

            if req.material_id in pending_map:
                existing = pending_map[req.material_id]
                if purchase_quantity <= 0:
                    crud_purchase_suggestion.delete_by_id(db, existing.id)
                    continue
                preferred_supplier = PurchaseService._get_best_supplier(
                    db, req.material_id, purchase_quantity
                )
                supplier_id = preferred_supplier.supplier_id if preferred_supplier else existing.suggested_supplier_id
                delivery_days = preferred_supplier.delivery_days if preferred_supplier else (
                    (existing.expected_delivery_date - date.today()).days
                    if existing.expected_delivery_date else 30
                )
                expected_date = date.today() + timedelta(days=max(1, delivery_days))
                reason = PurchaseService._build_reason(req, include_safety_stock)

                if (existing.suggested_quantity != purchase_quantity or
                    existing.suggested_supplier_id != supplier_id or
                    existing.reason != reason):
                    updated = crud_purchase_suggestion.update(db, db_obj=existing, obj_in={
                        "suggested_quantity": purchase_quantity,
                        "suggested_supplier_id": supplier_id,
                        "expected_delivery_date": expected_date,
                        "reason": reason,
                        "priority": req.priority,
                    })
                    result_suggestions.append(updated)
                else:
                    result_suggestions.append(existing)
                continue

            if purchase_quantity <= 0:
                continue

            preferred_supplier = PurchaseService._get_best_supplier(
                db, req.material_id, purchase_quantity
            )
            supplier_id = preferred_supplier.supplier_id if preferred_supplier else None
            delivery_days = preferred_supplier.delivery_days if preferred_supplier else 30
            expected_date = date.today() + timedelta(days=delivery_days)
            reason = PurchaseService._build_reason(req, include_safety_stock)

            suggestion_in = PurchaseSuggestionCreate(
                material_id=req.material_id,
                suggested_quantity=purchase_quantity,
                reason=reason,
                priority=req.priority,
                suggested_supplier_id=supplier_id,
                expected_delivery_date=expected_date,
                status="pending"
            )
            suggestion = crud_purchase_suggestion.create(db, obj_in=suggestion_in)
            result_suggestions.append(suggestion)

        for mat_id, existing in pending_map.items():
            if mat_id not in processed_material_ids:
                material = crud_material.get(db, mat_id)
                if material:
                    stock_qty = 0
                    from app.crud.purchase import crud_inventory_batch
                    stock_qty = crud_inventory_batch.get_total_stock(db, mat_id)
                    if stock_qty >= material.safety_stock:
                        crud_purchase_suggestion.delete_by_id(db, existing.id)
                        continue
                result_suggestions.append(existing)

        result_suggestions.sort(key=lambda s: (
            0 if (s.material and s.material.is_critical) else 1,
            -s.priority,
            -s.suggested_quantity
        ))
        return result_suggestions

    @staticmethod
    def _build_reason(req: MaterialRequirement, include_safety_stock: bool) -> str:
        reason_parts = []
        prod_need = max(0, req.required_quantity - max(0, req.stock_quantity - (req.safety_stock if include_safety_stock else 0)))
        if prod_need > 0:
            reason_parts.append(f"生产需求{prod_need}")
        if include_safety_stock:
            safety_need = max(0, req.safety_stock - req.stock_quantity)
            if safety_need > 0:
                reason_parts.append(f"安全库存补充{safety_need}")
        if req.in_transit_quantity > 0:
            reason_parts.append(f"在途{req.in_transit_quantity}")
        if req.pending_suggestion_quantity > 0:
            reason_parts.append(f"待处理建议{req.pending_suggestion_quantity}")
        if req.is_critical:
            reason_parts.append("关键物料")
        return "、".join(reason_parts) if reason_parts else "物料需求"

    @staticmethod
    def _evaluate_supplier_capacity(
        capacity: SupplyCapacity, required_quantity: int
    ) -> Tuple[float, int, bool]:
        stock = capacity.current_stock or 0
        daily = capacity.daily_capacity or 0
        days = capacity.delivery_days or 0
        production_capable = daily * days
        total_capable = stock + production_capable
        coverage_ratio = total_capable / required_quantity if required_quantity > 0 else 999.0
        can_cover = total_capable >= required_quantity
        actual_delivery_days = days
        if stock >= required_quantity:
            actual_delivery_days = max(1, min(days, 3))
        elif daily > 0:
            need_produce = max(0, required_quantity - stock)
            production_days = (need_produce + daily - 1) // daily
            actual_delivery_days = max(days, production_days)
        return coverage_ratio, actual_delivery_days, can_cover

    @staticmethod
    def _get_best_supplier(db: Session, material_id: int, required_quantity: int = 0):
        capacities = crud_supply_capacity.get_preferred_suppliers(db, material_id)
        all_capacities = crud_supply_capacity.get_by_material(db, material_id)
        if not capacities and not all_capacities:
            return None
        all_to_evaluate = capacities if capacities else all_capacities

        evaluated = []
        for cap in all_to_evaluate:
            coverage_ratio, actual_dd, can_cover = PurchaseService._evaluate_supplier_capacity(
                cap, required_quantity
            )
            pass_rate = cap.pass_rate or 0.0
            is_pref = 1 if cap.is_preferred else 0
            score = (
                (1.0 if can_cover else 0.0) * 1000 +
                min(coverage_ratio, 3.0) * 100 +
                is_pref * 80 +
                pass_rate * 50 -
                actual_dd * 2
            )
            evaluated.append((cap, score, coverage_ratio, actual_dd, can_cover))

        evaluated.sort(key=lambda x: -x[1])

        if evaluated:
            best_cap, _, _, _, _ = evaluated[0]
            all_evaluated = []
            for cap in all_capacities:
                coverage_ratio, actual_dd, can_cover = PurchaseService._evaluate_supplier_capacity(
                    cap, required_quantity
                )
                pass_rate = cap.pass_rate or 0.0
                is_pref = 1 if cap.is_preferred else 0
                score = (
                    (1.0 if can_cover else 0.0) * 1000 +
                    min(coverage_ratio, 3.0) * 100 +
                    is_pref * 80 +
                    pass_rate * 50 -
                    actual_dd * 2
                )
                all_evaluated.append((cap, score))
            all_evaluated.sort(key=lambda x: -x[1])
            return all_evaluated[0][0]
        return None

    @staticmethod
    def convert_suggestion_to_order(
        db: Session,
        suggestion_id: int,
        order_no: str,
        supplier_id: Optional[int] = None,
        quantity: Optional[int] = None,
        expected_date: Optional[date] = None
    ) -> PurchaseOrder:
        suggestion = crud_purchase_suggestion.get(db, suggestion_id)
        if not suggestion:
            raise ValueError(f"采购建议不存在: {suggestion_id}")
        if suggestion.status != "pending":
            raise ValueError(f"采购建议状态为 {suggestion.status}，不可转单")
        final_supplier_id = supplier_id or suggestion.suggested_supplier_id
        if not final_supplier_id:
            raise ValueError("必须指定供应商")
        final_quantity = quantity or suggestion.suggested_quantity

        capacity = crud_supply_capacity.get_by_supplier_and_material(
            db, final_supplier_id, suggestion.material_id
        )
        capacity_warning = None
        if capacity:
            coverage_ratio, actual_dd, can_cover = PurchaseService._evaluate_supplier_capacity(
                capacity, final_quantity
            )
            if not can_cover:
                stock = capacity.current_stock or 0
                daily = capacity.daily_capacity or 0
                days = capacity.delivery_days or 0
                max_able = stock + daily * days
                capacity_warning = (
                    f"供应商库存({stock})+{days}天产能({daily * days})={max_able}，"
                    f"无法覆盖需求{final_quantity}，缺口{final_quantity - max_able}"
                )
        else:
            actual_dd = 30

        final_date = expected_date or suggestion.expected_delivery_date or (
            date.today() + timedelta(days=actual_dd)
        )
        remark_parts = [f"由采购建议#{suggestion_id}生成"]
        if capacity_warning:
            remark_parts.append(capacity_warning)

        from app.schemas import PurchaseOrderCreate
        order_in = PurchaseOrderCreate(
            order_no=order_no,
            supplier_id=final_supplier_id,
            material_id=suggestion.material_id,
            quantity=final_quantity,
            expected_date=final_date,
            status="ordered",
            remark="；".join(remark_parts)
        )
        order = crud_purchase_order.create(db, obj_in=order_in)
        crud_purchase_suggestion.update(db, db_obj=suggestion, obj_in={"status": "converted"})
        return order
