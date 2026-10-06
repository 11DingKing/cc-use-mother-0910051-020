from sqlalchemy.orm import Session
from typing import List, Optional
from app.crud.base import CRUDBase
from app.models import (
    AllocationPlan, AllocationPlanItem, AllocationDiff,
    AlternativeRatio
)
from app.schemas import (
    AlternativeRatioCreate,
)


class CRUDAlternativeRatio(CRUDBase[AlternativeRatio, AlternativeRatioCreate, dict]):
    def get_by_alternative(self, db: Session, alternative_id: int) -> Optional[AlternativeRatio]:
        return db.query(AlternativeRatio).filter(
            AlternativeRatio.alternative_id == alternative_id,
            AlternativeRatio.is_active == True
        ).first()

    def get_any_by_alternative(self, db: Session, alternative_id: int) -> Optional[AlternativeRatio]:
        return db.query(AlternativeRatio).filter(
            AlternativeRatio.alternative_id == alternative_id
        ).first()

crud_alternative_ratio = CRUDAlternativeRatio(AlternativeRatio)


class CRUDAllocationPlan(CRUDBase[AllocationPlan, dict, dict]):
    def get_by_plan_no(self, db: Session, plan_no: str) -> Optional[AllocationPlan]:
        return db.query(AllocationPlan).filter(AllocationPlan.plan_no == plan_no).first()

    def get_active_by_material(
        self, db: Session, material_id: int
    ) -> List[AllocationPlan]:
        """占用库存的方案：草稿（求解中）或已冻结，取消/过期的不算"""
        return db.query(AllocationPlan).filter(
            AllocationPlan.scope_material_id == material_id,
            AllocationPlan.status.in_(["draft", "frozen"])
        ).all()

    def get_drafts(self, db: Session) -> List[AllocationPlan]:
        return db.query(AllocationPlan).filter(AllocationPlan.status == "draft").all()

    def create_plan_with_items(
        self,
        db: Session,
        *,
        plan_no: str,
        scope_material_id: int,
        strategy: str,
        created_by: Optional[str],
        snapshot: str,
        strategy_comparison: Optional[str],
        items: List[dict],
        remark: Optional[str] = None,
    ) -> AllocationPlan:
        plan = AllocationPlan(
            plan_no=plan_no,
            scope_material_id=scope_material_id,
            status="draft",
            strategy=strategy,
            created_by=created_by,
            snapshot=snapshot,
            strategy_comparison=strategy_comparison,
            remark=remark,
        )
        db.add(plan)
        db.flush()
        for item in items:
            db.add(AllocationPlanItem(plan_id=plan.id, **item))
        db.commit()
        db.refresh(plan)
        return plan

    def touch(self, db: Session, plan: AllocationPlan) -> AllocationPlan:
        plan.version = (plan.version or 1) + 1
        db.add(plan)
        db.commit()
        db.refresh(plan)
        return plan

crud_allocation_plan = CRUDAllocationPlan(AllocationPlan)


class CRUDAllocationPlanItem(CRUDBase[AllocationPlanItem, dict, dict]):
    def get_by_plan(self, db: Session, plan_id: int) -> List[AllocationPlanItem]:
        return db.query(AllocationPlanItem).filter(
            AllocationPlanItem.plan_id == plan_id
        ).all()

    def get_by_production_batch(
        self, db: Session, production_batch_id: int
    ) -> List[AllocationPlanItem]:
        return db.query(AllocationPlanItem).join(AllocationPlan).filter(
            AllocationPlanItem.production_batch_id == production_batch_id,
            AllocationPlan.status.in_(["draft", "frozen"])
        ).all()

crud_allocation_plan_item = CRUDAllocationPlanItem(AllocationPlanItem)


class CRUDAllocationDiff(CRUDBase[AllocationDiff, dict, dict]):
    def get_by_plan(self, db: Session, plan_id: int) -> List[AllocationDiff]:
        return db.query(AllocationDiff).filter(
            AllocationDiff.plan_id == plan_id
        ).order_by(AllocationDiff.id).all()

    def delete_by_plan(self, db: Session, plan_id: int) -> int:
        deleted = db.query(AllocationDiff).filter(
            AllocationDiff.plan_id == plan_id
        ).delete()
        return deleted

    def mark_resolved(self, db: Session, diff_id: int, resolution: str) -> Optional[AllocationDiff]:
        diff = self.get(db, diff_id)
        if diff:
            diff.status = resolution
            db.add(diff)
            db.commit()
            db.refresh(diff)
        return diff

crud_allocation_diff = CRUDAllocationDiff(AllocationDiff)
