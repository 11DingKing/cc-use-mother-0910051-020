from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional, Dict
from app.crud.base import CRUDBase
from app.models import (
    AllocationPlan, AllocationPlanItem, AllocationLine, AllocationDiffSuggestion
)


class CRUDAllocationPlan(CRUDBase[AllocationPlan, dict, dict]):
    def get_by_plan_no(self, db: Session, plan_no: str) -> Optional[AllocationPlan]:
        return db.query(AllocationPlan).filter(AllocationPlan.plan_no == plan_no).first()

    def get_active(self, db: Session) -> List[AllocationPlan]:
        return db.query(AllocationPlan).filter(
            AllocationPlan.status.in_(["draft", "partially_confirmed", "confirmed"])
        ).order_by(AllocationPlan.created_at.desc()).all()

    def get_reserving_plans(self, db: Session) -> List[AllocationPlan]:
        """已确认（含部分确认）的方案，其 confirmed 行构成库存承诺"""
        return db.query(AllocationPlan).filter(
            AllocationPlan.status.in_(["partially_confirmed", "confirmed"])
        ).order_by(AllocationPlan.confirmed_at).all()

crud_allocation_plan = CRUDAllocationPlan(AllocationPlan)


class CRUDAllocationPlanItem(CRUDBase[AllocationPlanItem, dict, dict]):
    def get_by_plan(self, db: Session, plan_id: int) -> List[AllocationPlanItem]:
        return db.query(AllocationPlanItem).filter(
            AllocationPlanItem.plan_id == plan_id
        ).all()

crud_allocation_plan_item = CRUDAllocationPlanItem(AllocationPlanItem)


class CRUDAllocationLine(CRUDBase[AllocationLine, dict, dict]):
    def get_by_plan(self, db: Session, plan_id: int) -> List[AllocationLine]:
        return db.query(AllocationLine).filter(AllocationLine.plan_id == plan_id).all()

    def sum_confirmed_by_inventory_batch(
        self, db: Session, exclude_plan_id: Optional[int] = None
    ) -> Dict[int, int]:
        """所有已确认分配行按库存批次汇总的占用量（库存批次自身单位）"""
        query = db.query(
            AllocationLine.inventory_batch_id,
            func.sum(AllocationLine.quantity)
        ).filter(
            AllocationLine.source_type == "inventory",
            AllocationLine.status == "confirmed",
            AllocationLine.inventory_batch_id.isnot(None)
        )
        if exclude_plan_id is not None:
            query = query.filter(AllocationLine.plan_id != exclude_plan_id)
        query = query.group_by(AllocationLine.inventory_batch_id)
        return {row[0]: int(row[1]) for row in query.all()}

    def sum_confirmed_by_purchase_order(
        self, db: Session, exclude_plan_id: Optional[int] = None
    ) -> Dict[int, int]:
        """所有已确认分配行按在途订单汇总的占用量"""
        query = db.query(
            AllocationLine.purchase_order_id,
            func.sum(AllocationLine.quantity)
        ).filter(
            AllocationLine.source_type == "in_transit",
            AllocationLine.status == "confirmed",
            AllocationLine.purchase_order_id.isnot(None)
        )
        if exclude_plan_id is not None:
            query = query.filter(AllocationLine.plan_id != exclude_plan_id)
        query = query.group_by(AllocationLine.purchase_order_id)
        return {row[0]: int(row[1]) for row in query.all()}

crud_allocation_line = CRUDAllocationLine(AllocationLine)


class CRUDAllocationDiffSuggestion(CRUDBase[AllocationDiffSuggestion, dict, dict]):
    def get_by_plan(self, db: Session, plan_id: int) -> List[AllocationDiffSuggestion]:
        return db.query(AllocationDiffSuggestion).filter(
            AllocationDiffSuggestion.plan_id == plan_id
        ).order_by(AllocationDiffSuggestion.created_at.desc()).all()

    def get_open_by_plan_and_type(
        self, db: Session, plan_id: int, change_type: str
    ) -> List[AllocationDiffSuggestion]:
        return db.query(AllocationDiffSuggestion).filter(
            AllocationDiffSuggestion.plan_id == plan_id,
            AllocationDiffSuggestion.change_type == change_type,
            AllocationDiffSuggestion.status == "open"
        ).all()

    def get_open(self, db: Session) -> List[AllocationDiffSuggestion]:
        return db.query(AllocationDiffSuggestion).filter(
            AllocationDiffSuggestion.status == "open"
        ).order_by(AllocationDiffSuggestion.created_at.desc()).all()

crud_allocation_diff_suggestion = CRUDAllocationDiffSuggestion(AllocationDiffSuggestion)
