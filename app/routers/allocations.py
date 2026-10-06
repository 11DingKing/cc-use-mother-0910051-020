from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.schemas import (
    AllocationSolveRequest,
    AllocationSolveResponse,
    AllocationPlanBase,
    AllocationPlanDetail,
    AllocationConfirmRequest,
    AllocationCancelRequest,
    AllocationRecheckResponse,
    AllocationDiffItem,
)
from app.services.allocation import AllocationService

router = APIRouter(prefix="/allocations", tags=["替代料分配方案"])


@router.post("/solve", response_model=AllocationSolveResponse)
def solve_allocation(req: AllocationSolveRequest, db: Session = Depends(get_db)):
    """综合车型允许范围、替代比例、批次优先级与交期，求解有限库存分配方案（草稿即预留库存）"""
    try:
        return AllocationService.solve(db, req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("", response_model=List[AllocationPlanBase])
def list_allocations(
    material_id: Optional[int] = None,
    status: Optional[str] = Query(None, description="draft/frozen/cancelled"),
    db: Session = Depends(get_db),
):
    return AllocationService.list_plans(db, material_id=material_id, status=status)


@router.get("/{plan_id}", response_model=AllocationPlanDetail)
def get_allocation(plan_id: int, db: Session = Depends(get_db)):
    try:
        return AllocationService.get_plan_detail(db, plan_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{plan_id}/confirm", response_model=AllocationPlanBase)
def confirm_allocation(plan_id: int, req: AllocationConfirmRequest,
                       db: Session = Depends(get_db)):
    if req.plan_id != plan_id:
        raise HTTPException(status_code=400, detail="路径与报文中的方案ID不一致")
    try:
        return AllocationService.confirm(
            db,
            plan_id=plan_id,
            confirmed_by=req.confirmed_by,
            accepted_batch_ids=req.accepted_batch_ids,
            remark=req.remark,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{plan_id}/cancel", response_model=AllocationPlanBase)
def cancel_allocation(plan_id: int, req: AllocationCancelRequest,
                      db: Session = Depends(get_db)):
    try:
        return AllocationService.cancel(
            db,
            plan_id=plan_id,
            cancelled_by=req.cancelled_by,
            reason=req.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{plan_id}/recheck", response_model=AllocationRecheckResponse)
def recheck_allocation(plan_id: int, db: Session = Depends(get_db)):
    """冻结后复核：库存或供应承诺变化只生成差异建议，不改写已确认结果"""
    try:
        return AllocationService.recheck(db, plan_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/diffs/{diff_id}/resolve", response_model=AllocationDiffItem)
def resolve_allocation_diff(
    diff_id: int,
    resolution: str = Query(..., description="accepted/ignored"),
    db: Session = Depends(get_db),
):
    try:
        return AllocationService.resolve_diff(db, diff_id, resolution)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
