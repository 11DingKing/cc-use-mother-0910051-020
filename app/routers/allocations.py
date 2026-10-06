from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.crud.allocation import crud_allocation_plan, crud_allocation_diff_suggestion
from app.schemas import (
    AllocationPlan, AllocationPlanDetail,
    AllocationSolveRequest, AllocationCompareRequest, AllocationCompareResult,
    AllocationConfirmRequest, AllocationCancelRequest,
    AllocationDiffSuggestion,
)
from app.services.allocation import AllocationService, AllocationConflictError

router = APIRouter(prefix="/allocations", tags=["替代料分配"])


@router.post("/solve", response_model=AllocationPlanDetail)
def solve_allocation(request: AllocationSolveRequest, db: Session = Depends(get_db)):
    """求解生成替代料分配方案（草稿）。草稿不占用库存，确认后才承诺。"""
    try:
        return AllocationService.solve(
            db,
            material_ids=request.material_ids,
            strategy=request.strategy,
            created_by=request.created_by,
            name=request.name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/compare", response_model=AllocationCompareResult)
def compare_strategies(request: AllocationCompareRequest, db: Session = Depends(get_db)):
    """对比全部分配策略：各策略下哪些批次按期/延期，以及策略间交期差异"""
    return AllocationService.compare_strategies(db, material_ids=request.material_ids)


@router.get("/", response_model=List[AllocationPlan])
def list_plans(status: Optional[str] = None, db: Session = Depends(get_db)):
    if status:
        return [p for p in crud_allocation_plan.get_active(db) if p.status == status]
    return crud_allocation_plan.get_active(db)


@router.get("/diff-suggestions/open", response_model=List[AllocationDiffSuggestion])
def list_open_diff_suggestions(db: Session = Depends(get_db)):
    return crud_allocation_diff_suggestion.get_open(db)


@router.post("/diff-suggestions/{suggestion_id}/dismiss", response_model=AllocationDiffSuggestion)
def dismiss_diff_suggestion(suggestion_id: int, db: Session = Depends(get_db)):
    suggestion = crud_allocation_diff_suggestion.get(db, suggestion_id)
    if not suggestion:
        raise HTTPException(status_code=404, detail="差异建议不存在")
    suggestion.status = "dismissed"
    db.add(suggestion)
    db.commit()
    db.refresh(suggestion)
    return suggestion


@router.get("/{plan_id}", response_model=AllocationPlanDetail)
def get_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = crud_allocation_plan.get(db, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="分配方案不存在")
    return plan


@router.post("/{plan_id}/confirm", response_model=AllocationPlanDetail)
def confirm_plan(
    plan_id: int,
    request: AllocationConfirmRequest,
    db: Session = Depends(get_db),
):
    """确认方案（可传 item_ids 部分确认）。确认即冻结并承诺库存。"""
    try:
        return AllocationService.confirm(
            db,
            plan_id=plan_id,
            version=request.version,
            confirmed_by=request.confirmed_by,
            item_ids=request.item_ids,
        )
    except AllocationConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{plan_id}/cancel", response_model=AllocationPlanDetail)
def cancel_plan(
    plan_id: int,
    request: AllocationCancelRequest,
    db: Session = Depends(get_db),
):
    """取消方案（或部分明细），释放库存承诺"""
    try:
        return AllocationService.cancel(
            db, plan_id=plan_id, version=request.version, item_ids=request.item_ids
        )
    except AllocationConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{plan_id}/diff-suggestions", response_model=List[AllocationDiffSuggestion])
def list_plan_diff_suggestions(plan_id: int, db: Session = Depends(get_db)):
    plan = crud_allocation_plan.get(db, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="分配方案不存在")
    return crud_allocation_diff_suggestion.get_by_plan(db, plan_id)
