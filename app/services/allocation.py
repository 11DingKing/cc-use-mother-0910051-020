import json
import math
from datetime import datetime, date
from typing import List, Optional, Dict, Tuple
from uuid import uuid4

from sqlalchemy.orm import Session

from app.crud.vehicle import crud_vehicle, crud_production_batch
from app.crud.alternative import crud_alternative_material, crud_alternative_restriction
from app.crud.purchase import crud_purchase_order, crud_inventory_batch
from app.crud.allocation import (
    crud_allocation_plan, crud_allocation_plan_item,
    crud_allocation_line, crud_allocation_diff_suggestion
)
from app.models import AllocationPlan, AllocationPlanItem, AllocationLine, AllocationDiffSuggestion
from app.schemas import (
    AllocationCompareResult, StrategyOutcome, StrategyBatchOutcome, DeliveryDateDiff
)

STRATEGY_PRIORITY_FIRST = "priority_first"
STRATEGY_DEADLINE_FIRST = "deadline_first"
STRATEGY_FAIR_SHARE = "fair_share"
ALLOCATION_STRATEGIES = (STRATEGY_PRIORITY_FIRST, STRATEGY_DEADLINE_FIRST, STRATEGY_FAIR_SHARE)

STRATEGY_LABELS = {
    STRATEGY_PRIORITY_FIRST: "车型优先级优先",
    STRATEGY_DEADLINE_FIRST: "交期优先",
    STRATEGY_FAIR_SHARE: "按比例公平分配",
}


class AllocationConflictError(Exception):
    """并发冲突：方案版本已变化，或库存已被其他已确认方案承诺"""


class AllocationService:
    """替代料分配方案服务。

    求解生成草稿方案（不占用库存）；计划员确认时在写事务中原子校验并冻结，
    已确认结果不会被库存变化或供应承诺更新悄悄改写，只生成差异建议。
    """

    # ---------------- 求解 ----------------

    @staticmethod
    def solve(
        db: Session,
        material_ids: Optional[List[int]] = None,
        strategy: str = STRATEGY_PRIORITY_FIRST,
        created_by: Optional[str] = None,
        name: Optional[str] = None,
    ) -> AllocationPlan:
        if strategy not in ALLOCATION_STRATEGIES:
            raise ValueError(
                f"未知分配策略: {strategy}，可选: {', '.join(ALLOCATION_STRATEGIES)}"
            )
        demands = AllocationService._collect_demands(db, material_ids)
        supply = AllocationService._build_supply_snapshot(db)
        result = AllocationService._run_allocation(db, demands, supply, strategy)

        plan_no = f"AP-{datetime.now():%Y%m%d%H%M%S}-{uuid4().hex[:6].upper()}"
        plan = AllocationPlan(
            plan_no=plan_no,
            name=name or f"{STRATEGY_LABELS[strategy]}方案",
            strategy=strategy,
            status="draft",
            version=1,
            created_by=created_by,
        )
        db.add(plan)
        db.flush()

        for item in result["items"]:
            d = item["demand"]
            db_item = AllocationPlanItem(
                plan_id=plan.id,
                production_batch_id=d["batch_id"],
                vehicle_model_id=d["vehicle_model_id"],
                material_id=d["material_id"],
                required_quantity=d["required_qty"],
                allocated_main_quantity=item["allocated_main"],
                allocated_alt_quantity=item["allocated_alt"],
                unmet_quantity=item["unmet"],
                status=item["status"],
                item_status="draft",
                unmet_reason=item["unmet_reason"],
                estimated_delay_days=item["estimated_delay_days"],
            )
            db.add(db_item)
            db.flush()
            for line in item["lines"]:
                db.add(AllocationLine(
                    plan_id=plan.id,
                    plan_item_id=db_item.id,
                    production_batch_id=d["batch_id"],
                    source_type=line["source_type"],
                    inventory_batch_id=line.get("inventory_batch_id"),
                    purchase_order_id=line.get("purchase_order_id"),
                    material_id=line["material_id"],
                    is_alternative=line["is_alternative"],
                    quantity=line["quantity"],
                    main_equivalent=line["main_equivalent"],
                    status="draft",
                ))
        db.commit()
        db.refresh(plan)
        return plan

    @staticmethod
    def compare_strategies(
        db: Session, material_ids: Optional[List[int]] = None
    ) -> AllocationCompareResult:
        """对所有策略分别在内存中求解（不落库），输出各策略下哪些批次交期不同"""
        demands = AllocationService._collect_demands(db, material_ids)
        strategy_results: Dict[str, dict] = {}
        for strategy in ALLOCATION_STRATEGIES:
            supply = AllocationService._build_supply_snapshot(db)
            strategy_results[strategy] = AllocationService._run_allocation(
                db, demands, supply, strategy
            )

        strategy_outcomes: List[StrategyOutcome] = []
        per_key: Dict[Tuple[int, int], Dict[str, dict]] = {}
        for strategy, result in strategy_results.items():
            outcomes: List[StrategyBatchOutcome] = []
            fully = partial = unmet_count = 0
            for item in result["items"]:
                d = item["demand"]
                if item["unmet"] <= 0:
                    fully += 1
                elif item["allocated_main"] + item["allocated_alt"] > 0:
                    partial += 1
                else:
                    unmet_count += 1
                outcomes.append(StrategyBatchOutcome(
                    production_batch_id=d["batch_id"],
                    batch_no=d["batch_no"],
                    vehicle_model_name=d["vehicle_model_name"],
                    material_id=d["material_id"],
                    material_name=d["material_name"],
                    plan_date=d["plan_date"],
                    required_quantity=d["required_qty"],
                    status=item["status"],
                    unmet_quantity=item["unmet"],
                    estimated_delay_days=item["estimated_delay_days"],
                ))
                per_key.setdefault((d["batch_id"], d["material_id"]), {})[strategy] = {
                    "status": item["status"],
                    "unmet_quantity": item["unmet"],
                    "estimated_delay_days": item["estimated_delay_days"],
                }
            strategy_outcomes.append(StrategyOutcome(
                strategy=strategy,
                fully_covered=fully,
                partially_covered=partial,
                unmet=unmet_count,
                outcomes=outcomes,
            ))

        delivery_diffs: List[DeliveryDateDiff] = []
        for (batch_id, material_id), by_strategy in per_key.items():
            signatures = {
                (v["status"], v["unmet_quantity"], v["estimated_delay_days"])
                for v in by_strategy.values()
            }
            if len(signatures) <= 1:
                continue
            sample = next(
                o for o in strategy_outcomes[0].outcomes
                if o.production_batch_id == batch_id and o.material_id == material_id
            )

            def delay_of(v: dict) -> float:
                if v["unmet_quantity"] <= 0:
                    return 0.0
                if v["estimated_delay_days"] is None:
                    return float("inf")
                return float(v["estimated_delay_days"])

            best = min(by_strategy, key=lambda s: delay_of(by_strategy[s]))
            worst = max(by_strategy, key=lambda s: delay_of(by_strategy[s]))
            delivery_diffs.append(DeliveryDateDiff(
                production_batch_id=batch_id,
                batch_no=sample.batch_no,
                material_id=material_id,
                material_name=sample.material_name,
                plan_date=sample.plan_date,
                by_strategy=by_strategy,
                best_strategy=best,
                worst_strategy=worst,
            ))
        delivery_diffs.sort(key=lambda x: (x.plan_date, x.batch_no))
        return AllocationCompareResult(
            strategies=strategy_outcomes, delivery_diffs=delivery_diffs
        )

    # ---------------- 确认 / 取消 ----------------

    @staticmethod
    def confirm(
        db: Session,
        plan_id: int,
        version: int,
        confirmed_by: Optional[str] = None,
        item_ids: Optional[List[int]] = None,
    ) -> AllocationPlan:
        """确认方案（可部分确认）。确认即承诺库存，冻结结果。

        并发安全：先在写事务中原子 bump 版本（SQLite 写锁使并发确认串行化），
        再校验库存未被其他已确认方案承诺，最后落库；任一失败整体回滚。
        """
        plan = crud_allocation_plan.get(db, plan_id)
        if not plan:
            raise ValueError(f"分配方案不存在: {plan_id}")
        if plan.status not in ("draft", "partially_confirmed"):
            raise ValueError(f"方案状态为 {plan.status}，不允许确认")
        if plan.version != version:
            raise AllocationConflictError(
                f"方案版本已变化（当前{plan.version}，请求{version}），请重新获取后再确认"
            )

        draft_items = [i for i in plan.items if i.item_status == "draft"]
        if item_ids is not None:
            id_set = set(item_ids)
            items = [i for i in draft_items if i.id in id_set]
            if not items:
                raise ValueError("选中的明细不存在或已处理")
        else:
            items = draft_items
        if not items:
            raise ValueError("没有可确认的明细")

        item_id_set = {i.id for i in items}
        lines = [
            l for l in plan.lines
            if l.plan_item_id in item_id_set and l.status == "draft"
        ]

        # 原子 bump 版本：乐观锁 + 立即获取写锁，使并发确认串行化
        updated = db.query(AllocationPlan).filter(
            AllocationPlan.id == plan_id,
            AllocationPlan.version == version,
            AllocationPlan.status.in_(["draft", "partially_confirmed"]),
        ).update({"version": version + 1}, synchronize_session=False)
        if updated == 0:
            db.rollback()
            raise AllocationConflictError("方案已被其他计划员修改，请重新获取后再确认")

        try:
            AllocationService._assert_lines_available(db, lines)
        except Exception:
            db.rollback()
            raise

        now = datetime.now()
        for line in lines:
            line.status = "confirmed"
            db.add(line)
        for item in items:
            item.item_status = "confirmed"
            db.add(item)

        db.flush()
        db.refresh(plan)
        plan.status = AllocationService._derive_plan_status(plan)
        plan.confirmed_by = confirmed_by
        plan.confirmed_at = now
        db.add(plan)
        db.commit()
        db.refresh(plan)
        return plan

    @staticmethod
    def cancel(
        db: Session,
        plan_id: int,
        version: Optional[int] = None,
        item_ids: Optional[List[int]] = None,
    ) -> AllocationPlan:
        """取消方案（或部分明细），释放其占用的库存承诺"""
        plan = crud_allocation_plan.get(db, plan_id)
        if not plan:
            raise ValueError(f"分配方案不存在: {plan_id}")
        if plan.status == "cancelled":
            raise ValueError("方案已取消")
        if version is not None and plan.version != version:
            raise AllocationConflictError(
                f"方案版本已变化（当前{plan.version}，请求{version}），请重新获取后再取消"
            )

        active_items = [i for i in plan.items if i.item_status != "cancelled"]
        if item_ids is not None:
            id_set = set(item_ids)
            items = [i for i in active_items if i.id in id_set]
            if not items:
                raise ValueError("选中的明细不存在或已取消")
        else:
            items = active_items

        item_id_set = {i.id for i in items}
        for line in plan.lines:
            if line.plan_item_id in item_id_set and line.status != "cancelled":
                line.status = "cancelled"
                db.add(line)
        for item in items:
            item.item_status = "cancelled"
            db.add(item)

        plan.version += 1
        db.flush()
        db.refresh(plan)
        plan.status = AllocationService._derive_plan_status(plan)
        db.add(plan)
        db.commit()
        db.refresh(plan)
        return plan

    @staticmethod
    def _derive_plan_status(plan: AllocationPlan) -> str:
        statuses = {i.item_status for i in plan.items}
        if statuses == {"cancelled"}:
            return "cancelled"
        if "draft" in statuses:
            return "partially_confirmed" if "confirmed" in statuses else "draft"
        return "confirmed"

    @staticmethod
    def _assert_lines_available(db: Session, lines: List[AllocationLine]) -> None:
        """校验待确认行不超过 物理可用 − 其他已确认方案占用"""
        need_inv: Dict[int, int] = {}
        need_po: Dict[int, int] = {}
        for line in lines:
            if line.source_type == "inventory":
                need_inv[line.inventory_batch_id] = need_inv.get(line.inventory_batch_id, 0) + line.quantity
            else:
                need_po[line.purchase_order_id] = need_po.get(line.purchase_order_id, 0) + line.quantity

        reserved_inv = crud_allocation_line.sum_confirmed_by_inventory_batch(db)
        reserved_po = crud_allocation_line.sum_confirmed_by_purchase_order(db)

        for inv_id, qty in need_inv.items():
            batch = crud_inventory_batch.get(db, inv_id)
            physical = 0
            if batch and not batch.is_quarantined:
                physical = batch.available_quantity or 0
            usable = physical - reserved_inv.get(inv_id, 0)
            if usable < qty:
                raise AllocationConflictError(
                    f"库存批次#{inv_id}可用量不足：需要{qty}，仅剩{max(usable, 0)}"
                    f"（可能已被其他已确认方案承诺），请重新求解"
                )
        for po_id, qty in need_po.items():
            order = crud_purchase_order.get(db, po_id)
            if not order or order.status not in ("ordered", "partial"):
                raise AllocationConflictError(
                    f"采购订单#{po_id}已不在途，无法承诺，请重新求解"
                )
            from app.models import Delivery
            delivered = db.query(Delivery).filter(Delivery.purchase_order_id == po_id).all()
            remaining = max(0, order.quantity - sum(d.quantity for d in delivered))
            usable = remaining - reserved_po.get(po_id, 0)
            if usable < qty:
                raise AllocationConflictError(
                    f"采购订单#{po_id}在途余量不足：需要{qty}，仅剩{max(usable, 0)}"
                    f"（可能已被其他已确认方案承诺），请重新求解"
                )

    # ---------------- 差异建议 ----------------

    @staticmethod
    def validate_confirmed_plans(
        db: Session,
        trigger: str = "inventory_changed",
        material_ids: Optional[List[int]] = None,
    ) -> List[AllocationDiffSuggestion]:
        """库存变化或供应承诺更新后调用：校验已确认方案，只生成差异建议，不改写已确认结果"""
        plans = crud_allocation_plan.get_reserving_plans(db)
        if not plans:
            return []

        reserved_inv = crud_allocation_line.sum_confirmed_by_inventory_batch(db)
        reserved_po = crud_allocation_line.sum_confirmed_by_purchase_order(db)

        # 库存批次当前物理可用
        physical_inv: Dict[int, int] = {}
        for inv_id in reserved_inv:
            batch = crud_inventory_batch.get(db, inv_id)
            physical_inv[inv_id] = (
                (batch.available_quantity or 0)
                if batch and not batch.is_quarantined else 0
            )
        # 在途订单当前剩余
        remaining_po: Dict[int, int] = {}
        for po_id in reserved_po:
            order = crud_purchase_order.get(db, po_id)
            if order and order.status in ("ordered", "partial"):
                from app.models import Delivery
                delivered = db.query(Delivery).filter(
                    Delivery.purchase_order_id == po_id
                ).all()
                remaining_po[po_id] = max(
                    0, order.quantity - sum(d.quantity for d in delivered)
                )
            else:
                remaining_po[po_id] = 0

        # 按方案确认顺序累计占用，超出物理可用的部分视为该方案受影响
        over_by_plan: Dict[int, List[dict]] = {}
        running_inv: Dict[int, int] = {}
        running_po: Dict[int, int] = {}
        for plan in plans:
            for line in plan.lines:
                if line.status != "confirmed":
                    continue
                if material_ids and line.material_id not in material_ids:
                    continue
                if line.source_type == "inventory":
                    key = line.inventory_batch_id
                    running_inv[key] = running_inv.get(key, 0) + line.quantity
                    if running_inv[key] > physical_inv.get(key, 0):
                        over_by_plan.setdefault(plan.id, []).append({
                            "line_id": line.id,
                            "source": "inventory",
                            "inventory_batch_id": key,
                            "material_id": line.material_id,
                            "quantity": line.quantity,
                            "physical_available": physical_inv.get(key, 0),
                        })
                else:
                    key = line.purchase_order_id
                    running_po[key] = running_po.get(key, 0) + line.quantity
                    if running_po[key] > remaining_po.get(key, 0):
                        over_by_plan.setdefault(plan.id, []).append({
                            "line_id": line.id,
                            "source": "in_transit",
                            "purchase_order_id": key,
                            "material_id": line.material_id,
                            "quantity": line.quantity,
                            "remaining_in_transit": remaining_po.get(key, 0),
                        })

        suggestions: List[AllocationDiffSuggestion] = []
        for plan in plans:
            problems = over_by_plan.get(plan.id, [])
            if problems:
                description = (
                    f"方案{plan.plan_no}有{len(problems)}条已确认分配"
                    f"因{'库存变化' if trigger == 'inventory_changed' else '供应承诺更新'}"
                    f"不再被库存/在途覆盖，建议重新求解或调整"
                )
                suggestions.append(AllocationService._upsert_suggestion(
                    db, plan, trigger, description, {"affected_lines": problems}
                ))
            else:
                # 供应恢复/库存回补：之前落选（未满足）的明细现在可能可满足
                recoverable = AllocationService._find_recoverable_items(db, plan)
                if recoverable:
                    description = (
                        f"方案{plan.plan_no}有{len(recoverable)}条未满足明细"
                        f"在当前供应下可覆盖，建议重新求解"
                    )
                    suggestions.append(AllocationService._upsert_suggestion(
                        db, plan, trigger, description,
                        {"recoverable_items": recoverable}
                    ))
        return [s for s in suggestions if s is not None]

    @staticmethod
    def _upsert_suggestion(
        db: Session,
        plan: AllocationPlan,
        change_type: str,
        description: str,
        detail: dict,
    ) -> Optional[AllocationDiffSuggestion]:
        detail_json = json.dumps(detail, ensure_ascii=False, default=str)
        existing = crud_allocation_diff_suggestion.get_open_by_plan_and_type(
            db, plan.id, change_type
        )
        for old in existing:
            if old.detail == detail_json:
                return old  # 相同建议已存在，不重复生成
            old.status = "dismissed"  # 旧建议已过时，由新建议取代
            db.add(old)
        suggestion = AllocationDiffSuggestion(
            plan_id=plan.id,
            change_type=change_type,
            description=description,
            detail=detail_json,
            status="open",
        )
        db.add(suggestion)
        db.commit()
        db.refresh(suggestion)
        return suggestion

    @staticmethod
    def _find_recoverable_items(db: Session, plan: AllocationPlan) -> List[dict]:
        """检查方案中未满足的明细，在当前可用供应（物理 − 全部已确认占用）下是否已可覆盖"""
        unmet_items = [
            i for i in plan.items
            if i.item_status == "confirmed" and i.unmet_quantity > 0
        ]
        if not unmet_items:
            return []
        reserved_inv = crud_allocation_line.sum_confirmed_by_inventory_batch(db)
        recoverable = []
        for item in unmet_items:
            batches = crud_inventory_batch.get_available_batches(db, item.material_id)
            usable = sum(
                b.available_quantity - reserved_inv.get(b.id, 0) for b in batches
            )
            usable = max(0, usable)
            if usable >= item.unmet_quantity:
                recoverable.append({
                    "item_id": item.id,
                    "production_batch_id": item.production_batch_id,
                    "material_id": item.material_id,
                    "unmet_quantity": item.unmet_quantity,
                    "usable_now": usable,
                })
        return recoverable

    # ---------------- 需求与供应快照 ----------------

    @staticmethod
    def _collect_demands(
        db: Session, material_ids: Optional[List[int]] = None
    ) -> List[dict]:
        batches = crud_production_batch.get_planned_batches_sorted(db)
        material_name_cache: Dict[int, str] = {}
        demands: List[dict] = []
        for batch in batches:
            bom_items = crud_vehicle.get_bom_items(db, batch.vehicle_model_id)
            for bom in bom_items:
                if material_ids and bom.material_id not in material_ids:
                    continue
                if bom.material_id not in material_name_cache:
                    material_name_cache[bom.material_id] = (
                        bom.material.name if bom.material else "未知物料"
                    )
                vm = batch.vehicle_model
                demands.append({
                    "batch_id": batch.id,
                    "batch_no": batch.batch_no,
                    "vehicle_model_id": batch.vehicle_model_id,
                    "vehicle_model_name": vm.name if vm else "未知车型",
                    "vehicle_priority": vm.priority if vm else 0,
                    "plan_date": batch.plan_date,
                    "material_id": bom.material_id,
                    "material_name": material_name_cache[bom.material_id],
                    "required_qty": bom.quantity * batch.quantity,
                })
        return demands

    @staticmethod
    def _build_supply_snapshot(db: Session) -> dict:
        """供应快照：物理可用 − 其他已确认方案占用。草稿方案不占用库存。"""
        reserved_inv = crud_allocation_line.sum_confirmed_by_inventory_batch(db)
        reserved_po = crud_allocation_line.sum_confirmed_by_purchase_order(db)

        inventory: Dict[int, List[dict]] = {}
        from app.models import InventoryBatch
        batches = db.query(InventoryBatch).filter(
            InventoryBatch.is_quarantined == False,
            InventoryBatch.available_quantity > 0,
        ).all()
        for batch in batches:
            usable = batch.available_quantity - reserved_inv.get(batch.id, 0)
            if usable <= 0:
                continue
            inventory.setdefault(batch.material_id, []).append({
                "batch_id": batch.id,
                "available": usable,
                "expire_date": batch.expire_date,
            })
        # FEFO：先到期先用，无到期日的排最后
        for rows in inventory.values():
            rows.sort(key=lambda x: (x["expire_date"] is None, x["expire_date"] or date.max, x["batch_id"]))

        transit: Dict[int, List[dict]] = {}
        from app.models import PurchaseOrder
        orders = db.query(PurchaseOrder).filter(
            PurchaseOrder.status.in_(["ordered", "partial"])
        ).all()
        from app.models import Delivery
        for order in orders:
            delivered = db.query(Delivery).filter(
                Delivery.purchase_order_id == order.id
            ).all()
            remaining = max(0, order.quantity - sum(d.quantity for d in delivered))
            remaining -= reserved_po.get(order.id, 0)
            if remaining <= 0:
                continue
            transit.setdefault(order.material_id, []).append({
                "order_id": order.id,
                "expected_date": order.expected_date,
                "remaining": remaining,
            })
        for rows in transit.values():
            rows.sort(key=lambda x: (x["expected_date"], x["order_id"]))

        return {"inventory": inventory, "transit": transit}

    # ---------------- 分配引擎 ----------------

    @staticmethod
    def _run_allocation(
        db: Session, demands: List[dict], supply: dict, strategy: str
    ) -> dict:
        alt_cache: Dict[int, list] = {}

        def get_alts(material_id: int):
            if material_id not in alt_cache:
                alt_cache[material_id] = crud_alternative_material.get_alternatives_for_material(
                    db, material_id
                )
            return alt_cache[material_id]

        def allowed_alts(material_id: int, vehicle_model_id: int):
            return [
                alt for alt in get_alts(material_id)
                if crud_alternative_restriction.is_alternative_allowed(
                    db, alt.id, vehicle_model_id
                )
            ]

        def sort_key(d: dict):
            if strategy == STRATEGY_PRIORITY_FIRST:
                return (-d["vehicle_priority"], d["plan_date"], d["batch_id"], d["material_id"])
            return (d["plan_date"], -d["vehicle_priority"], d["batch_id"], d["material_id"])

        ordered = sorted(demands, key=sort_key)

        # fair_share：按物料计算可满足比例，第一遍每个需求只取按比例分配的份额
        fair_caps: Dict[Tuple[int, int], int] = {}
        if strategy == STRATEGY_FAIR_SHARE:
            by_material: Dict[int, List[dict]] = {}
            for d in demands:
                by_material.setdefault(d["material_id"], []).append(d)
            for material_id, ds in by_material.items():
                total_req = sum(d["required_qty"] for d in ds)
                if total_req <= 0:
                    continue
                main_avail = sum(x["available"] for x in supply["inventory"].get(material_id, []))
                main_avail += sum(x["remaining"] for x in supply["transit"].get(material_id, []))
                alt_equiv = 0.0
                seen_alt_materials = set()
                for alt in get_alts(material_id):
                    if alt.alternative_material_id in seen_alt_materials:
                        continue
                    seen_alt_materials.add(alt.alternative_material_id)
                    stock = sum(
                        x["available"]
                        for x in supply["inventory"].get(alt.alternative_material_id, [])
                    )
                    ratio = alt.substitution_ratio if alt.substitution_ratio and alt.substitution_ratio > 0 else 1.0
                    alt_equiv += stock / ratio
                ratio_fill = min(1.0, (main_avail + alt_equiv) / total_req)
                for d in ds:
                    fair_caps[(d["batch_id"], d["material_id"])] = int(d["required_qty"] * ratio_fill)

        consumption_log: Dict[int, List[Tuple[str, int]]] = {}
        items: List[dict] = []

        def allocate_one(d: dict, cap: Optional[int], allow_alt: bool = True) -> dict:
            required = d["required_qty"]
            target = required if cap is None else min(required, max(cap, 0))
            material_id = d["material_id"]
            plan_date = d["plan_date"]
            lines: List[dict] = []
            main_allocated = 0
            alt_allocated = 0

            # 1. 主料库存（FEFO）
            for inv in supply["inventory"].get(material_id, []):
                if main_allocated >= target:
                    break
                if inv["available"] <= 0:
                    continue
                take = min(inv["available"], target - main_allocated)
                inv["available"] -= take
                main_allocated += take
                lines.append({
                    "source_type": "inventory",
                    "inventory_batch_id": inv["batch_id"],
                    "material_id": material_id,
                    "is_alternative": False,
                    "quantity": take,
                    "main_equivalent": take,
                })

            # 2. 按期在途（计划日期前能到货的）
            for tr in supply["transit"].get(material_id, []):
                if main_allocated >= target:
                    break
                if tr["expected_date"] > plan_date or tr["remaining"] <= 0:
                    continue
                take = min(tr["remaining"], target - main_allocated)
                tr["remaining"] -= take
                main_allocated += take
                lines.append({
                    "source_type": "in_transit",
                    "purchase_order_id": tr["order_id"],
                    "material_id": material_id,
                    "is_alternative": False,
                    "quantity": take,
                    "main_equivalent": take,
                })

            # 3. 替代料（受车型允许范围、替代比例上限、换算比例约束）
            gap = target - main_allocated
            alt_limit_note = None
            if gap > 0 and allow_alt:
                alts = allowed_alts(material_id, d["vehicle_model_id"])
                if alts:
                    max_pct = min(
                        (a.max_substitution_percent if a.max_substitution_percent is not None else 100)
                        for a in alts
                    )
                    alt_cap_equiv = (required * max_pct) // 100
                    for alt in alts:
                        if gap <= 0 or alt_allocated >= alt_cap_equiv:
                            break
                        ratio = alt.substitution_ratio if alt.substitution_ratio and alt.substitution_ratio > 0 else 1.0
                        for inv in supply["inventory"].get(alt.alternative_material_id, []):
                            if gap <= 0 or alt_allocated >= alt_cap_equiv:
                                break
                            if inv["available"] <= 0:
                                continue
                            equiv_room = min(gap, alt_cap_equiv - alt_allocated)
                            take_equiv = min(equiv_room, int(inv["available"] / ratio))
                            if take_equiv <= 0:
                                continue
                            take_qty = min(inv["available"], math.ceil(take_equiv * ratio))
                            actual_equiv = int(take_qty / ratio)
                            if actual_equiv <= 0:
                                continue
                            inv["available"] -= take_qty
                            alt_allocated += actual_equiv
                            gap -= actual_equiv
                            lines.append({
                                "source_type": "inventory",
                                "inventory_batch_id": inv["batch_id"],
                                "material_id": alt.alternative_material_id,
                                "is_alternative": True,
                                "quantity": take_qty,
                                "main_equivalent": actual_equiv,
                            })
                    if alt_allocated >= alt_cap_equiv and gap > 0:
                        alt_limit_note = max_pct

            allocated_total = main_allocated + alt_allocated
            unmet = required - allocated_total
            if unmet <= 0:
                status = "covered_by_alternative" if alt_allocated > 0 else "covered"
            elif allocated_total > 0:
                status = "partial"
            else:
                status = "unmet"

            estimated_delay = None
            earliest_date = None
            if unmet > 0:
                cumulative = 0
                for tr in sorted(
                    supply["transit"].get(material_id, []),
                    key=lambda x: (x["expected_date"], x["order_id"]),
                ):
                    if tr["remaining"] <= 0:
                        continue
                    cumulative += tr["remaining"]
                    if cumulative >= unmet:
                        earliest_date = max(tr["expected_date"], plan_date)
                        break
                if earliest_date is not None:
                    estimated_delay = max(0, (earliest_date - plan_date).days)

            unmet_reason = None
            if unmet > 0:
                unmet_reason = AllocationService._build_unmet_reason(
                    db, d, unmet, alt_limit_note, earliest_date, estimated_delay,
                    consumption_log, get_alts, allowed_alts, supply,
                )

            if allocated_total > 0:
                consumption_log.setdefault(material_id, []).append(
                    (d["batch_no"], allocated_total)
                )

            return {
                "demand": d,
                "allocated_main": main_allocated,
                "allocated_alt": alt_allocated,
                "unmet": unmet,
                "status": status,
                "unmet_reason": unmet_reason,
                "estimated_delay_days": estimated_delay,
                "lines": lines,
            }

        for d in ordered:
            cap = None
            if strategy == STRATEGY_FAIR_SHARE:
                cap = fair_caps.get((d["batch_id"], d["material_id"]), 0)
            items.append(allocate_one(d, cap))

        if strategy == STRATEGY_FAIR_SHARE:
            # 第二遍：剩余主料库存/在途按同一顺序补给仍有缺口的批次
            for item in items:
                if item["unmet"] <= 0:
                    continue
                d = item["demand"]
                top_up_demand = dict(d)
                top_up_demand["required_qty"] = item["unmet"]
                extra = allocate_one(top_up_demand, None, allow_alt=False)
                if extra["allocated_main"] > 0:
                    item["allocated_main"] += extra["allocated_main"]
                    item["unmet"] -= extra["allocated_main"]
                    item["lines"].extend(extra["lines"])
                    if item["unmet"] <= 0:
                        item["status"] = (
                            "covered_by_alternative" if item["allocated_alt"] > 0 else "covered"
                        )
                        item["unmet_reason"] = None
                        item["estimated_delay_days"] = None
                    else:
                        item["status"] = "partial"

        return {"items": items}

    @staticmethod
    def _build_unmet_reason(
        db: Session,
        d: dict,
        unmet: int,
        alt_limit_note: Optional[int],
        earliest_date: Optional[date],
        estimated_delay: Optional[int],
        consumption_log: Dict[int, List[Tuple[str, int]]],
        get_alts,
        allowed_alts,
        supply: dict,
    ) -> str:
        material_id = d["material_id"]
        parts = [f"主料《{d['material_name']}》按期供应不足，缺口{unmet}件"]

        all_alts = get_alts(material_id)
        if not all_alts:
            parts.append("未配置可用替代料")
        else:
            allowed = allowed_alts(material_id, d["vehicle_model_id"])
            if not allowed:
                parts.append(f"车型《{d['vehicle_model_name']}》不允许使用替代料")
            else:
                for alt in allowed:
                    alt_material = alt.alternative_material
                    alt_name = alt_material.name if alt_material else f"#{alt.alternative_material_id}"
                    stock = sum(
                        x["available"]
                        for x in supply["inventory"].get(alt.alternative_material_id, [])
                    )
                    if stock <= 0:
                        parts.append(f"替代料《{alt_name}》可用库存不足")
                if alt_limit_note is not None:
                    parts.append(f"替代比例上限{alt_limit_note}%，超出部分不可用替代料补齐")

        consumers = consumption_log.get(material_id, [])
        if consumers:
            top = sorted(consumers, key=lambda x: -x[1])[:3]
            parts.append(
                "库存已优先分配给：" + "、".join(f"{bn}({qty}件)" for bn, qty in top)
            )

        if estimated_delay is not None and earliest_date is not None:
            parts.append(
                f"最早{earliest_date.isoformat()}可补齐，预计延期{estimated_delay}天"
            )
        else:
            parts.append("在途供应也不足，无法估计补齐日期")
        return "；".join(parts)
