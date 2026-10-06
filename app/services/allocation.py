"""
替代料有限库存分配服务

核心职责：
1. 把主料及替代料的"可用批次"（库存批次 / 在途采购单 / 供应商承诺分批）
   按车型允许范围、替代比例、批次优先级、交期分配到具体生产批次；
2. 求解时把其他进行中（draft）或已冻结（frozen）方案的占用视为已承诺库存，
   进程锁 + 行锁保证两个计划员并发求解不会重复承诺同一库存；
3. 计划员确认（支持部分确认）后冻结；取消则释放占用；
4. 冻结后库存/供应承诺变化只生成 AllocationDiff 差异建议，绝不改写已确认结果；
5. 同时按多种策略求解，说明改用其他策略会影响哪些批次交期。
"""
import json
import math
import threading
import uuid
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.crud.allocation import (
    crud_allocation_plan,
    crud_allocation_plan_item,
    crud_allocation_diff,
    crud_alternative_ratio,
)
from app.crud.alternative import (
    crud_alternative_material,
    crud_alternative_restriction,
)
from app.crud.purchase import crud_purchase_order
from app.crud.vehicle import crud_production_batch
from app.crud.material import crud_material
from app.models import (
    AllocationPlan,
    AllocationPlanItem,
    AllocationDiff,
    InventoryBatch,
    PurchaseOrder,
    SupplierConfirmationBatch,
    Delivery,
)
from app.schemas import (
    AllocationSolveRequest,
    AllocationSolveResponse,
    AllocationPlanBase,
    AllocationPlanDetail,
    AllocationPlanItemOut,
    AllocationDiffItem,
    AllocationItemResult,
    AllocationSourceView,
    StrategyComparison,
    StrategyImpactBatch,
    AllocationRecheckResponse,
)

EPS = 1e-9

STRATEGY_NAMES = {
    "priority_due_date": "优先级与交期综合（默认）",
    "due_date": "交期优先",
    "priority_first": "车型优先级绝对优先",
}
ALL_STRATEGIES = list(STRATEGY_NAMES.keys())

# 方案状态：draft（求解完成待确认，已占用库存）/ frozen（已确认冻结）/ cancelled（已取消）
ACTIVE_STATUSES = ("draft", "frozen")


class _LockRegistry:
    """按物料维度的进程内互斥锁，串行化同一物料的并发求解/确认/取消"""

    def __init__(self):
        self._guard = threading.Lock()
        self._locks: Dict[str, threading.RLock] = {}

    def get(self, key: str) -> threading.RLock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._locks[key] = lock
            return lock


_LOCK_REGISTRY = _LockRegistry()


# ============================ 供应池 ============================

class _Source:
    __slots__ = ("source_type", "source_id", "material_id", "available_date", "quantity")

    def __init__(self, source_type: str, source_id: int, material_id: int,
                 available_date: Optional[date], quantity: int):
        self.source_type = source_type
        self.source_id = source_id
        self.material_id = material_id
        self.available_date = available_date  # None 表示现有库存，即时可用
        self.quantity = quantity

    @property
    def key(self) -> Tuple[str, int]:
        return (self.source_type, self.source_id)


class _Pool:
    """单个物料的时间维度供应池，持有量 = 其他方案占用 + 本次求解已分配"""

    def __init__(self, material_id: int):
        self.material_id = material_id
        self.sources: List[_Source] = []
        self.external_holds: Dict[Tuple[str, int], int] = {}

    def add_source(self, source: _Source):
        self.sources.append(source)

    def _order_key(self, s: _Source):
        # 现货（None）排在最前，其次按到货日期
        return (1, date.max) if s.available_date is None else (0, s.available_date)

    def available_of(self, source: _Source, working_holds: dict) -> int:
        held = self.external_holds.get(source.key, 0) + working_holds.get(source.key, 0)
        return max(0, source.quantity - held)

    def total_available_by(self, by_date: date, working_holds: dict) -> int:
        total = 0
        for s in self.sources:
            if s.available_date is None or s.available_date <= by_date:
                total += self.available_of(s, working_holds)
        return total

    def consume(self, quantity: int, by_date: date,
                working_holds: dict) -> List[Tuple[_Source, int]]:
        """按到货时间先到先得，返回实际占用的 (来源, 数量) 列表"""
        drawn: List[Tuple[_Source, int]] = []
        remaining = quantity
        for source in sorted(self.sources, key=self._order_key):
            if remaining <= 0:
                break
            if source.available_date is not None and source.available_date > by_date:
                continue
            avail = self.available_of(source, working_holds)
            if avail <= 0:
                continue
            take = min(avail, remaining)
            working_holds[source.key] = working_holds.get(source.key, 0) + take
            drawn.append((source, take))
            remaining -= take
        return drawn


# ============================ 求解器 ============================

class _Solver:
    def __init__(self, db: Session, scope_material_id: int,
                 external_holds: Dict[Tuple[str, int], int],
                 include_commitments: bool = True):
        self.db = db
        self.scope_material_id = scope_material_id
        self.include_commitments = include_commitments
        self.pools: Dict[int, _Pool] = {}
        self.material_cache: Dict[int, object] = {}
        self.ratio_cache: Dict[int, Tuple[float, Optional[float]]] = {}
        self._build_pools(external_holds)

    # ---------- 数据装载 ----------

    def _material(self, material_id: int):
        if material_id not in self.material_cache:
            self.material_cache[material_id] = crud_material.get(self.db, material_id)
        return self.material_cache[material_id]

    def _ensure_pool(self, material_id: int) -> _Pool:
        pool = self.pools.get(material_id)
        if pool is None:
            pool = _Pool(material_id)
            self.pools[material_id] = pool
        return pool

    def _build_pools(self, external_holds: Dict[Tuple[str, int], int]):
        needed = {self.scope_material_id}
        alternatives = crud_alternative_material.get_alternatives_for_material(
            self.db, self.scope_material_id
        )
        alt_options = []
        for alt in alternatives:
            needed.add(alt.alternative_material_id)
            ratio = crud_alternative_ratio.get_by_alternative(self.db, alt.id)
            self.ratio_cache[alt.id] = (
                ratio.substitution_ratio if ratio else 1.0,
                ratio.max_share if ratio else None,
            )
            alt_options.append(alt)
        self._alt_options = alt_options

        for material_id in needed:
            pool = self._ensure_pool(material_id)

            batches = (
                self.db.query(InventoryBatch)
                .filter(
                    InventoryBatch.material_id == material_id,
                    InventoryBatch.is_quarantined == False,  # noqa: E712
                    InventoryBatch.available_quantity > 0,
                )
                .all()
            )
            for b in batches:
                pool.add_source(_Source("inventory", b.id, material_id, None, b.available_quantity))

            orders = crud_purchase_order.get_in_transit_by_material_ordered_by_date(
                self.db, material_id
            )
            for o in orders:
                pool.add_source(_Source(
                    "purchase_order", o["order_id"], material_id,
                    o["expected_date"], o["remaining_quantity"]
                ))

            if self.include_commitments:
                from app.models import SupplierConfirmation
                conf_rows = (
                    self.db.query(SupplierConfirmationBatch, SupplierConfirmation)
                    .join(
                        SupplierConfirmation,
                        SupplierConfirmation.id == SupplierConfirmationBatch.confirmation_id,
                    )
                    .filter(
                        SupplierConfirmation.material_id == material_id,
                        SupplierConfirmation.status.in_(["confirmed", "shortage"]),
                    )
                    .all()
                )
                for cb, conf in conf_rows:
                    pool.add_source(_Source(
                        "confirmation_batch", cb.id, material_id,
                        cb.planned_date, cb.quantity
                    ))

            for key, qty in external_holds.items():
                if any(s.key == key for s in pool.sources):
                    pool.external_holds[key] = qty

    # ---------- 需求 ----------

    def _load_batches(self, production_batch_ids: Optional[List[int]]):
        if production_batch_ids:
            rows = [crud_production_batch.get(self.db, bid) for bid in production_batch_ids]
            batches = [b for b in rows if b is not None]
        else:
            batches = crud_production_batch.get_planned_batches_sorted(self.db)

        demand = []
        for pb in batches:
            if not pb or pb.status != "planned":
                continue
            bom_qty = crud_production_batch.get_bom_quantity(
                self.db, pb.vehicle_model_id, self.scope_material_id
            )
            if not bom_qty:
                continue
            demand.append((pb, bom_qty, bom_qty * pb.quantity))
        return demand

    @staticmethod
    def _order_demand(demand, strategy: str):
        def key(item):
            pb, _, _ = item
            priority = pb.vehicle_model.priority if pb.vehicle_model else 0
            if strategy == "due_date":
                return (pb.plan_date, -priority, pb.id)
            if strategy == "priority_first":
                return (-priority, pb.plan_date, pb.id)
            # 默认综合策略：车型优先级每高一级，等效交期提前一天，
            # 使高优先级批次在交期相近时可先分得有限库存，但交期差距大时仍以交期为先
            effective_date = pb.plan_date - timedelta(days=priority)
            return (effective_date, pb.plan_date, -priority, pb.id)
        return sorted(demand, key=key)

    # ---------- 替代料 ----------

    def _allowed_alternatives(self, vehicle_model_id: int):
        result = []
        for alt in self._alt_options:
            if not crud_alternative_restriction.is_alternative_allowed(
                self.db, alt.id, vehicle_model_id
            ):
                result.append((alt, False))
            else:
                result.append((alt, True))
        # 允许的按替代优先级，其后再排不允许的（仅用于落选说明）
        result.sort(key=lambda x: (0 if x[1] else 1, x[0].priority))
        return result

    # ---------- 主求解 ----------

    def solve(self, production_batch_ids: Optional[List[int]],
              strategy: str) -> Tuple[List[AllocationItemResult], List[dict], List[dict]]:
        demand = self._order_demand(self._load_batches(production_batch_ids), strategy)
        working_holds: Dict[Tuple[str, int], int] = {}
        results: List[AllocationItemResult] = []
        item_rows: List[dict] = []

        for pb, bom_qty, required in demand:
            vm = pb.vehicle_model
            sources_used: List[dict] = []
            rejected: List[dict] = []

            # 第一步：主料按时可用量
            main_pool = self.pools[self.scope_material_id]
            main_drawn = main_pool.consume(required, pb.plan_date, working_holds)
            main_got = sum(q for _, q in main_drawn)
            for src, qty in main_drawn:
                sources_used.append(self._source_view(src, qty, qty, None))
                item_rows.append(self._item_row(pb, src, qty, qty, None))
            remaining = required - main_got
            alt_equiv_by_alt: Dict[int, int] = {}

            used_alternative = False
            # 第二步：按替代优先级使用车型允许的替代料
            if remaining > 0:
                for alt, allowed in self._allowed_alternatives(pb.vehicle_model_id):
                    if remaining <= 0:
                        break
                    ratio, max_share = self.ratio_cache[alt.id]
                    if not allowed:
                        rejected.append({
                            "alternative_id": alt.id,
                            "alternative_material_id": alt.alternative_material_id,
                            "alternative_material_name":
                                self._material(alt.alternative_material_id).name
                                if self._material(alt.alternative_material_id) else "未知",
                            "reason": "该车型不允许使用此替代料",
                        })
                        continue

                    alt_pool = self.pools.get(alt.alternative_material_id)
                    need_alt_units = math.ceil(remaining / ratio - EPS)
                    if max_share is not None:
                        share_cap = math.floor(required * max_share)
                        # 该替代料在本批次已用当量（顺序分配下每个替代关系只经过一次）
                        need_alt_units = min(
                            need_alt_units,
                            math.ceil(max(0, share_cap) / ratio - EPS),
                        )
                    if need_alt_units <= 0:
                        rejected.append({
                            "alternative_id": alt.id,
                            "alternative_material_id": alt.alternative_material_id,
                            "alternative_material_name":
                                self._material(alt.alternative_material_id).name
                                if self._material(alt.alternative_material_id) else "未知",
                            "reason": f"受最大替代比例限制（上限{int((max_share or 0) * 100)}%）",
                        })
                        continue

                    drawn = alt_pool.consume(
                        need_alt_units, pb.plan_date, working_holds
                    ) if alt_pool else []
                    alt_got_units = sum(q for _, q in drawn)
                    total_equiv = min(
                        remaining,
                        int(math.floor(alt_got_units * ratio + EPS)),
                    )
                    # 把总当量按各来源实物量比例拆分；余数按小数部分大小分配，
                    # 且每个来源不超过 ceil(其实物当量)，避免逐段取整高估
                    raw = [q * ratio for _, q in drawn]
                    floors = [int(math.floor(x + EPS)) for x in raw]
                    leftover = total_equiv - sum(floors)
                    equivs = list(floors)
                    frac_order = sorted(
                        range(len(equivs)),
                        key=lambda i: raw[i] - floors[i],
                        reverse=True,
                    )
                    for i in frac_order:
                        if leftover <= 0:
                            break
                        equivs[i] += 1
                        leftover -= 1
                    for (src, qty), equiv in zip(drawn, equivs):
                        if equiv <= 0:
                            continue
                        sources_used.append(self._source_view(src, qty, equiv, alt.id))
                        item_rows.append(self._item_row(pb, src, qty, equiv, alt.id))
                        remaining -= equiv
                        alt_equiv_by_alt[alt.id] = alt_equiv_by_alt.get(alt.id, 0) + equiv
                        used_alternative = True

                    available_units = (
                        alt_pool.total_available_by(pb.plan_date, working_holds)
                        if alt_pool else 0
                    )
                    if remaining > 0 and alt_got_units < need_alt_units:
                        name = (
                            self._material(alt.alternative_material_id).name
                            if self._material(alt.alternative_material_id) else "未知"
                        )
                        rejected.append({
                            "alternative_id": alt.id,
                            "alternative_material_id": alt.alternative_material_id,
                            "alternative_material_name": name,
                            "available_equivalent": int((alt_got_units + available_units) * ratio),
                            "reason": (
                                f"替代料按时可用当量约{int((alt_got_units + available_units) * ratio)}，"
                                f"不足剩余缺口{remaining}（可能已被优先级更高/交期更早的批次占用）"
                            ),
                        })
                    elif remaining > 0 and max_share is not None:
                        rejected.append({
                            "alternative_id": alt.id,
                            "alternative_material_id": alt.alternative_material_id,
                            "alternative_material_name":
                                self._material(alt.alternative_material_id).name
                                if self._material(alt.alternative_material_id) else "未知",
                            "reason": f"已达最大替代比例上限（{int(max_share * 100)}%），仍有缺口{remaining}",
                        })

            # 第三步：判定结果
            main_mat = self._material(self.scope_material_id)
            if remaining <= 0:
                status = "covered_by_alternative" if used_alternative else "covered"
                on_time = True
                fulfillable_date = None
                reason = None
            else:
                on_time = False
                fulfillable_date = self._earliest_fulfillable_date(
                    pb, remaining, working_holds, alt_equiv_by_alt
                )
                if fulfillable_date is None:
                    status = "unfulfillable"
                    reason = (
                        f"主料及所有车型允许的替代料总供应不足，按时缺口{remaining}，"
                        f"且现有在途/承诺全部到货后仍无法补齐"
                    )
                else:
                    status = "delayed"
                    delay_days = (fulfillable_date - pb.plan_date).days
                    reason = (
                        f"按时缺口{remaining}；主料按时可用{main_got}"
                        + (f"，已动用允许的替代料" if used_alternative else "")
                        + f"；最早{fulfillable_date.isoformat()}可补齐，预计延期{delay_days}天"
                    )

            allocated_equiv = required - remaining
            results.append(AllocationItemResult(
                production_batch_id=pb.id,
                production_batch_no=pb.batch_no,
                vehicle_model_id=pb.vehicle_model_id,
                vehicle_model_name=vm.name if vm else "未知",
                plan_date=pb.plan_date,
                batch_priority=vm.priority if vm else 0,
                requirement_material_id=self.scope_material_id,
                requirement_material_code=main_mat.code if main_mat else "",
                requirement_material_name=main_mat.name if main_mat else "未知",
                required_quantity=required,
                allocated_equivalent=allocated_equiv,
                shortage_quantity=remaining,
                on_time=on_time,
                fulfillable_date=fulfillable_date,
                sources=sources_used,
                status=status,
                reason=reason,
                rejected_reasons=rejected,
            ))

        source_views = self._source_views(working_holds)
        return results, item_rows, source_views

    def _earliest_fulfillable_date(
        self, pb, remaining: int, working_holds: dict,
        alt_used_equivalent: Optional[Dict[int, int]] = None,
    ) -> Optional[date]:
        """剩余缺口最早可补齐日期：主料与该车型允许的替代料（按换算比例、替代上限）合并时间线"""
        if remaining <= 0:
            return pb.plan_date
        alt_used_equivalent = alt_used_equivalent or {}

        # (date, equivalent_qty) 汇总；现货日期记为 plan_date（即时可用）
        timeline: Dict[date, int] = {}

        def add_pool(pool: _Pool, ratio: float, equiv_cap: Optional[int] = None):
            added = 0
            for src in sorted(pool.sources, key=pool._order_key):
                avail = pool.available_of(src, working_holds)
                if avail <= 0:
                    continue
                equiv = int(math.floor(avail * ratio + EPS))
                if equiv_cap is not None:
                    equiv = min(equiv, max(0, equiv_cap - added))
                if equiv <= 0:
                    continue
                d = src.available_date if src.available_date is not None else pb.plan_date
                timeline[d] = timeline.get(d, 0) + equiv
                added += equiv
                if equiv_cap is not None and added >= equiv_cap:
                    break

        add_pool(self.pools[self.scope_material_id], 1.0)
        for alt, allowed in self._allowed_alternatives(pb.vehicle_model_id):
            if not allowed:
                continue
            ratio, max_share = self.ratio_cache[alt.id]
            pool = self.pools.get(alt.alternative_material_id)
            if not pool:
                continue
            required = None
            if max_share is not None:
                bom_qty = crud_production_batch.get_bom_quantity(
                    self.db, pb.vehicle_model_id, self.scope_material_id
                )
                total_required = (bom_qty or 0) * pb.quantity
                share_cap = math.floor(total_required * max_share)
                required = max(0, share_cap - alt_used_equivalent.get(alt.id, 0))
            add_pool(pool, ratio, required)

        cumulative = 0
        for d in sorted(timeline.keys()):
            cumulative += timeline[d]
            if cumulative >= remaining:
                return d if d > pb.plan_date else pb.plan_date
        return None

    def _source_view(self, src: _Source, allocated: int,
                     equivalent: int, alternative_id: Optional[int]) -> dict:
        mat = self._material(src.material_id)
        return {
            "source_type": src.source_type,
            "source_id": src.source_id,
            "material_id": src.material_id,
            "material_code": mat.code if mat else "",
            "material_name": mat.name if mat else "未知",
            "available_date": src.available_date.isoformat() if src.available_date else None,
            "allocated_quantity": allocated,
            "equivalent_quantity": equivalent,
            "alternative_id": alternative_id,
        }

    def _item_row(self, pb, src: _Source, allocated: int,
                  equivalent: int, alternative_id: Optional[int]) -> dict:
        return {
            "production_batch_id": pb.id,
            "vehicle_model_id": pb.vehicle_model_id,
            "requirement_material_id": self.scope_material_id,
            "source_type": src.source_type,
            "source_id": src.source_id,
            "source_material_id": src.material_id,
            "alternative_id": alternative_id,
            "allocated_quantity": allocated,
            "equivalent_quantity": equivalent,
            "available_date": src.available_date,
        }

    def _source_views(self, working_holds: dict) -> List[dict]:
        views = []
        for material_id, pool in self.pools.items():
            mat = self._material(material_id)
            for src in sorted(pool.sources, key=pool._order_key):
                held = pool.external_holds.get(src.key, 0) + working_holds.get(src.key, 0)
                views.append({
                    "source_type": src.source_type,
                    "source_id": src.source_id,
                    "source_material_id": material_id,
                    "source_material_code": mat.code if mat else "",
                    "source_material_name": mat.name if mat else "未知",
                    "available_date": src.available_date.isoformat() if src.available_date else None,
                    "total_quantity": src.quantity,
                    "remaining_quantity": max(0, src.quantity - held),
                    "held_quantity": held,
                })
        return views


# ============================ 服务入口 ============================

class AllocationService:

    # ---------- 求解 ----------

    @staticmethod
    def solve(db: Session, req: AllocationSolveRequest) -> AllocationSolveResponse:
        material = crud_material.get(db, req.material_id)
        if not material:
            raise ValueError(f"物料不存在: {req.material_id}")
        if req.strategy not in STRATEGY_NAMES:
            raise ValueError(f"未知分配策略: {req.strategy}，可选: {', '.join(ALL_STRATEGIES)}")

        lock = _LOCK_REGISTRY.get(f"allocation:material:{req.material_id}")
        with lock:
            # 行级排它锁（多进程部署下由数据库保证；SQLite 下为空操作，进程锁兜底）
            locked = (
                db.query(type(material))
                .filter(type(material).id == req.material_id)
                .with_for_update()
                .first()
            )
            if locked is None:
                raise ValueError(f"物料不存在: {req.material_id}")

            external_holds = AllocationService._load_external_holds(db)

            solver = _Solver(
                db, req.material_id, external_holds,
                include_commitments=req.include_supplier_commitments,
            )
            results, item_rows, source_views = solver.solve(
                req.production_batch_ids, req.strategy
            )

            # 多策略对比（只读求解，不落库、不增加占用）
            comparison = None
            strategy_metrics = {}
            if req.compare_strategies:
                outcomes = {}
                for strat in ALL_STRATEGIES:
                    s = _Solver(
                        db, req.material_id, external_holds,
                        include_commitments=req.include_supplier_commitments,
                    )
                    res, _, _ = s.solve(req.production_batch_ids, strat)
                    outcomes[strat] = res
                    strategy_metrics[strat] = AllocationService._metrics(strat, res)

                base_by_batch = {r.production_batch_id: r for r in outcomes[req.strategy]}
                # 与"交期优先"对比；若基准本身就是交期优先，则与"优先级绝对优先"对比
                other = "due_date" if req.strategy != "due_date" else "priority_first"
                impacts = []
                for r in outcomes[other]:
                    base = base_by_batch.get(r.production_batch_id)
                    if base is None:
                        continue
                    if (
                        base.on_time != r.on_time
                        or base.shortage_quantity != r.shortage_quantity
                        or base.fulfillable_date != r.fulfillable_date
                    ):
                        impacts.append(StrategyImpactBatch(
                            production_batch_id=r.production_batch_id,
                            production_batch_no=r.production_batch_no,
                            base_status=base.status,
                            alt_status=r.status,
                            base_on_time=base.on_time,
                            alt_on_time=r.on_time,
                            base_shortage=base.shortage_quantity,
                            alt_shortage=r.shortage_quantity,
                            base_fulfillable_date=base.fulfillable_date,
                            alt_fulfillable_date=r.fulfillable_date,
                        ))
                comparison = StrategyComparison(
                    base_strategy=req.strategy,
                    compared_strategy=other,
                    impacts=impacts,
                    summaries=[
                        {
                            "strategy": k,
                            "strategy_name": STRATEGY_NAMES[k],
                            **v,
                        }
                        for k, v in strategy_metrics.items()
                    ],
                )

            plan_no = req.plan_no or AllocationService._gen_plan_no(req.material_id)
            if crud_allocation_plan.get_by_plan_no(db, plan_no):
                raise ValueError(f"方案编号已存在: {plan_no}")

            snapshot = json.dumps({
                "generated_at": datetime.now().isoformat(),
                "strategy": req.strategy,
                "results": [r.model_dump(mode="json") for r in results],
                "sources": source_views,
                "strategy_metrics": strategy_metrics,
            }, ensure_ascii=False)

            comparison_text = (
                json.dumps(comparison.model_dump(mode="json"), ensure_ascii=False)
                if comparison else None
            )

            warning = None
            active = crud_allocation_plan.get_active_by_material(db, req.material_id)
            draft_count = sum(1 for p in active if p.status == "draft")
            if draft_count > 0:
                warning = (
                    f"物料{material.code}已有{draft_count}个待确认草稿方案，"
                    f"其库存占用已计入本次求解；请提醒相关计划员尽快确认或取消，"
                    f"避免草稿长期占用库存"
                )

            plan = crud_allocation_plan.create_plan_with_items(
                db,
                plan_no=plan_no,
                scope_material_id=req.material_id,
                strategy=req.strategy,
                created_by=req.created_by,
                snapshot=snapshot,
                strategy_comparison=comparison_text,
                items=item_rows,
                remark=warning,
            )

            return AllocationSolveResponse(
                plan=AllocationPlanBase.model_validate(plan),
                strategy=req.strategy,
                results=results,
                sources=[AllocationSourceView(**v) for v in source_views],
                strategy_comparison=comparison,
                warning=warning,
            )

    @staticmethod
    def _metrics(strategy: str, results: List[AllocationItemResult]) -> dict:
        unsatisfied = [r for r in results if not r.on_time]
        delayed = [
            r for r in unsatisfied
            if r.fulfillable_date and r.fulfillable_date > r.plan_date
        ]
        weighted = sum(
            (r.fulfillable_date - r.plan_date).days
            for r in delayed
        )
        return {
            "satisfied_batch_count": len(results) - len(unsatisfied),
            "unsatisfied_batch_count": len(unsatisfied),
            "total_shortage_quantity": sum(r.shortage_quantity for r in unsatisfied),
            "weighted_delay_days": weighted,
        }

    @staticmethod
    def _gen_plan_no(material_id: int) -> str:
        return f"AP{date.today().strftime('%Y%m%d')}-M{material_id}-{uuid.uuid4().hex[:6].upper()}"

    @staticmethod
    def _load_external_holds(db: Session) -> Dict[Tuple[str, int], int]:
        """所有 draft/frozen 方案对供应来源的占用量"""
        rows = (
            db.query(AllocationPlanItem.source_type, AllocationPlanItem.source_id,
                     AllocationPlanItem.allocated_quantity)
            .join(AllocationPlan, AllocationPlan.id == AllocationPlanItem.plan_id)
            .filter(AllocationPlan.status.in_(ACTIVE_STATUSES))
            .all()
        )
        holds: Dict[Tuple[str, int], int] = {}
        for source_type, source_id, qty in rows:
            key = (source_type, source_id)
            holds[key] = holds.get(key, 0) + qty
        return holds

    # ---------- 查询 ----------

    @staticmethod
    def list_plans(
        db: Session,
        material_id: Optional[int] = None,
        status: Optional[str] = None,
    ) -> List[AllocationPlan]:
        query = db.query(AllocationPlan)
        if material_id is not None:
            query = query.filter(AllocationPlan.scope_material_id == material_id)
        if status:
            query = query.filter(AllocationPlan.status == status)
        return query.order_by(AllocationPlan.created_at.desc()).all()

    @staticmethod
    def get_plan_detail(db: Session, plan_id: int) -> AllocationPlanDetail:
        plan = crud_allocation_plan.get(db, plan_id)
        if not plan:
            raise ValueError(f"分配方案不存在: {plan_id}")

        snapshot = json.loads(plan.snapshot) if plan.snapshot else {}
        results = [AllocationItemResult(**r) for r in snapshot.get("results", [])]
        sources = [AllocationSourceView(**v) for v in snapshot.get("sources", [])]
        comparison = None
        if plan.strategy_comparison:
            comparison = StrategyComparison(**json.loads(plan.strategy_comparison))

        return AllocationPlanDetail(
            **AllocationPlanBase.model_validate(plan).model_dump(),
            items=[AllocationPlanItemOut.model_validate(i)
                   for i in crud_allocation_plan_item.get_by_plan(db, plan_id)],
            diffs=[AllocationService._diff_view(db, d)
                   for d in crud_allocation_diff.get_by_plan(db, plan_id)],
            results=results,
            strategy_comparison=comparison,
            sources=sources,
        )

    # ---------- 确认 / 部分确认 ----------

    @staticmethod
    def confirm(
        db: Session,
        plan_id: int,
        confirmed_by: str,
        accepted_batch_ids: Optional[List[int]] = None,
        remark: Optional[str] = None,
    ) -> AllocationPlan:
        lock = _LOCK_REGISTRY.get(f"allocation:plan:{plan_id}")
        with lock:
            plan = crud_allocation_plan.get(db, plan_id)
            if not plan:
                raise ValueError(f"分配方案不存在: {plan_id}")
            if plan.status != "draft":
                raise ValueError(f"仅待确认(draft)方案可确认，当前状态: {plan.status}")
            if not confirmed_by:
                raise ValueError("确认人不能为空")

            material_lock = _LOCK_REGISTRY.get(f"allocation:material:{plan.scope_material_id}")
            with material_lock:
                # 冻结前防御性校验：方案明细没有与其他活跃方案超额共占同一来源
                items = crud_allocation_plan_item.get_by_plan(db, plan_id)
                if not AllocationService._items_still_satisfiable(db, items, plan_id):
                    raise ValueError(
                        "方案占用的库存已被其他已确认方案占用或库存已减少，"
                        "请重新求解后再确认"
                    )

                # 部分确认：剔除计划员未接受的批次，释放其库存占用
                rejected_batch_ids = set()
                if accepted_batch_ids is not None:
                    accepted = set(accepted_batch_ids)
                    rejected_batch_ids = {
                        it.production_batch_id for it in items
                        if it.production_batch_id not in accepted
                    }
                    for it in items:
                        if it.production_batch_id in rejected_batch_ids:
                            db.delete(it)

                snapshot = json.loads(plan.snapshot) if plan.snapshot else {}
                for r in snapshot.get("results", []):
                    if r["production_batch_id"] in rejected_batch_ids:
                        r["status"] = "rejected_in_confirmation"
                        r["on_time"] = False
                        r["reason"] = "计划员部分确认时未接受该批次，已释放其预留库存"
                snapshot["partial_confirmation"] = bool(rejected_batch_ids)
                snapshot["confirmed_rejected_batch_ids"] = sorted(rejected_batch_ids)

                plan.status = "frozen"
                plan.confirmed_by = confirmed_by
                plan.confirmed_at = datetime.now()
                plan.snapshot = json.dumps(snapshot, ensure_ascii=False)
                if remark:
                    plan.remark = (plan.remark or "") + f"｜确认备注: {remark}"
                plan.version += 1
                db.add(plan)
                db.commit()
                db.refresh(plan)
                return plan

    @staticmethod
    def _items_still_satisfiable(db: Session, items: List[AllocationPlanItem],
                                 plan_id: int) -> bool:
        """冻结前防御性校验：各来源当前可用量（扣除其他活跃方案占用）仍 >= 本方案占用"""
        other_holds: Dict[Tuple[str, int], int] = {}
        rows = (
            db.query(AllocationPlanItem.source_type, AllocationPlanItem.source_id,
                     AllocationPlanItem.allocated_quantity)
            .join(AllocationPlan, AllocationPlan.id == AllocationPlanItem.plan_id)
            .filter(
                AllocationPlan.status.in_(ACTIVE_STATUSES),
                AllocationPlan.id != plan_id,
            )
            .all()
        )
        for source_type, source_id, qty in rows:
            key = (source_type, source_id)
            other_holds[key] = other_holds.get(key, 0) + qty

        own: Dict[Tuple[str, int], int] = {}
        source_meta: Dict[Tuple[str, int], Tuple[str, int]] = {}
        for it in items:
            key = (it.source_type, it.source_id)
            own[key] = own.get(key, 0) + it.allocated_quantity
            source_meta[key] = (it.source_type, it.source_material_id)

        for key, qty in own.items():
            source_type, source_id = key
            if source_type == "inventory":
                b = db.query(InventoryBatch).filter(InventoryBatch.id == source_id).first()
                if not b or b.is_quarantined:
                    return False
                current = b.available_quantity
            elif source_type == "purchase_order":
                po = db.query(PurchaseOrder).filter(PurchaseOrder.id == source_id).first()
                if not po or po.status not in ("ordered", "partial"):
                    return False
                delivered = db.query(Delivery).filter(
                    Delivery.purchase_order_id == po.id
                ).all()
                current = max(0, po.quantity - sum(d.quantity for d in delivered))
            elif source_type == "confirmation_batch":
                cb = db.query(SupplierConfirmationBatch).filter(
                    SupplierConfirmationBatch.id == source_id
                ).first()
                if not cb:
                    return False
                current = cb.quantity
            else:
                continue
            if current - other_holds.get(key, 0) < qty:
                return False
        return True

    # ---------- 取消 ----------

    @staticmethod
    def cancel(
        db: Session,
        plan_id: int,
        cancelled_by: str,
        reason: Optional[str] = None,
    ) -> AllocationPlan:
        lock = _LOCK_REGISTRY.get(f"allocation:plan:{plan_id}")
        with lock:
            plan = crud_allocation_plan.get(db, plan_id)
            if not plan:
                raise ValueError(f"分配方案不存在: {plan_id}")
            if plan.status == "cancelled":
                raise ValueError("方案已取消，无需重复操作")
            if not cancelled_by:
                raise ValueError("取消人不能为空")
            plan.status = "cancelled"
            plan.cancelled_by = cancelled_by
            plan.cancelled_at = datetime.now()
            plan.cancel_reason = reason
            plan.version += 1
            db.add(plan)
            db.commit()
            db.refresh(plan)
            # 明细保留用于审计；活跃占用查询只统计 draft/frozen，取消即释放
            return plan

    # ---------- 冻结后差异复核 ----------

    @staticmethod
    def recheck(db: Session, plan_id: int) -> AllocationRecheckResponse:
        plan = crud_allocation_plan.get(db, plan_id)
        if not plan:
            raise ValueError(f"分配方案不存在: {plan_id}")
        if plan.status != "frozen":
            raise ValueError(f"仅已冻结(frozen)方案可复核差异，当前状态: {plan.status}")

        lock = _LOCK_REGISTRY.get(f"allocation:material:{plan.scope_material_id}")
        with lock:
            crud_allocation_diff.delete_by_plan(db, plan_id)

            items = crud_allocation_plan_item.get_by_plan(db, plan_id)

            diffs: List[AllocationDiff] = []

            def add_diff(diff_type, *, item=None, material_id=None,
                         quantity_change=0, detail="", source_type=None,
                         source_id=None, batch_id=None):
                diffs.append(AllocationDiff(
                    plan_id=plan_id,
                    production_batch_id=batch_id or (item.production_batch_id if item else None),
                    diff_type=diff_type,
                    source_type=source_type or (item.source_type if item else None),
                    source_id=source_id or (item.source_id if item else None),
                    material_id=material_id or (item.source_material_id if item else None),
                    quantity_change=quantity_change,
                    detail=detail,
                    status="suggested",
                ))

            for item in items:
                if item.source_type == "inventory":
                    b = db.query(InventoryBatch).filter(
                        InventoryBatch.id == item.source_id
                    ).first()
                    if not b:
                        add_diff("source_missing", item=item,
                                 detail=f"库存批次{item.source_id}已不存在，"
                                        f"原预留{item.allocated_quantity}需重新安排")
                    elif b.is_quarantined:
                        add_diff("source_quarantined", item=item,
                                 quantity_change=-item.allocated_quantity,
                                 detail=f"库存批次已被隔离（{b.quarantine_reason or '质量问题'}），"
                                        f"影响{item.allocated_quantity}件预留")
                    elif b.available_quantity < item.allocated_quantity:
                        add_diff("source_reduced", item=item,
                                 quantity_change=b.available_quantity - item.allocated_quantity,
                                 detail=f"库存可用量由{item.allocated_quantity}降为"
                                        f"{b.available_quantity}，缺口"
                                        f"{item.allocated_quantity - b.available_quantity}")
                elif item.source_type == "purchase_order":
                    po = db.query(PurchaseOrder).filter(
                        PurchaseOrder.id == item.source_id
                    ).first()
                    if not po:
                        add_diff("source_missing", item=item,
                                 detail=f"采购订单{item.source_id}已不存在")
                    elif po.status not in ("ordered", "partial"):
                        add_diff("source_closed", item=item,
                                 detail=f"采购订单{po.order_no}状态已变为{po.status}，"
                                        f"如已到货请核对库存批次")
                    else:
                        delivered = db.query(Delivery).filter(
                            Delivery.purchase_order_id == po.id
                        ).all()
                        remaining = max(0, po.quantity - sum(d.quantity for d in delivered))
                        if remaining < item.allocated_quantity:
                            add_diff("source_reduced", item=item,
                                     quantity_change=remaining - item.allocated_quantity,
                                     detail=f"在途剩余量由{item.allocated_quantity}降为{remaining}")
                        if po.expected_date and item.available_date and \
                                po.expected_date != item.available_date:
                            add_diff("date_changed", item=item,
                                     detail=f"承诺交期由{item.available_date.isoformat()}"
                                            f"变更为{po.expected_date.isoformat()}")
                elif item.source_type == "confirmation_batch":
                    cb = db.query(SupplierConfirmationBatch).filter(
                        SupplierConfirmationBatch.id == item.source_id
                    ).first()
                    if not cb:
                        add_diff("source_missing", item=item,
                                 detail="供应商承诺分批已被删除")
                    else:
                        if cb.planned_date and item.available_date and \
                                cb.planned_date != item.available_date:
                            add_diff("date_changed", item=item,
                                     detail=f"供应商承诺交期由{item.available_date.isoformat()}"
                                            f"变更为{cb.planned_date.isoformat()}")
                        if cb.quantity < item.allocated_quantity:
                            add_diff("source_reduced", item=item,
                                     quantity_change=cb.quantity - item.allocated_quantity,
                                     detail=f"供应商承诺数量由{item.allocated_quantity}"
                                            f"降为{cb.quantity}")

            # 新增供应（主料及涉及的替代料）：只提示，不动方案。
            # 以求解时快照中的全部供应来源为基线，求解时已存在但未被占用的不算新增。
            baseline_snapshot = json.loads(plan.snapshot) if plan.snapshot else {}
            baseline_keys = {
                (v.get("source_type"), v.get("source_id"))
                for v in baseline_snapshot.get("sources", [])
            }
            referenced: Dict[int, set] = {}
            material_ids = {plan.scope_material_id}
            for item in items:
                material_ids.add(item.source_material_id)
                referenced.setdefault(item.source_material_id, set()).add(
                    (item.source_type, item.source_id)
                )
            # 快照基线里还可能包含已从明细中剔除（部分确认）的来源
            for v in baseline_snapshot.get("sources", []):
                if v.get("source_material_id") is not None:
                    material_ids.add(v["source_material_id"])

            for material_id in material_ids:
                mat = crud_material.get(db, material_id)
                current_sources: List[Tuple[str, int, int, str]] = []
                for b in db.query(InventoryBatch).filter(
                    InventoryBatch.material_id == material_id,
                    InventoryBatch.is_quarantined == False,  # noqa: E712
                ).all():
                    current_sources.append(
                        ("inventory", b.id, b.available_quantity,
                         f"库存批次新增/现有可用{b.available_quantity}")
                    )
                for o in crud_purchase_order.get_in_transit_by_material_ordered_by_date(
                    db, material_id
                ):
                    current_sources.append(
                        ("purchase_order", o["order_id"], o["remaining_quantity"],
                         f"在途订单{o['remaining_quantity']}，预计"
                         f"{o['expected_date'].isoformat()}到货")
                    )
                from app.models import SupplierConfirmation
                conf_rows = (
                    db.query(SupplierConfirmationBatch, SupplierConfirmation)
                    .join(SupplierConfirmation,
                          SupplierConfirmation.id == SupplierConfirmationBatch.confirmation_id)
                    .filter(
                        SupplierConfirmation.material_id == material_id,
                        SupplierConfirmation.status.in_(["confirmed", "shortage"]),
                    ).all()
                )
                for cb, conf in conf_rows:
                    current_sources.append(
                        ("confirmation_batch", cb.id, cb.quantity,
                         f"供应商{conf.confirmation_no}承诺{cb.quantity}，"
                         f"{cb.planned_date.isoformat() if cb.planned_date else '日期未定'}交付")
                    )

                used_keys = referenced.get(material_id, set()) | baseline_keys
                for stype, sid, qty, desc in current_sources:
                    if (stype, sid) not in used_keys and qty > 0:
                        add_diff(
                            "supply_added",
                            material_id=material_id,
                            source_type=stype,
                            source_id=sid,
                            quantity_change=qty,
                            detail=f"{mat.name if mat else ''}出现方案外新增供应：{desc}，"
                                   f"可重新求解生成新方案，系统不会自动改写当前冻结结果",
                        )

            for d in diffs:
                db.add(d)
            db.commit()

            view = [AllocationService._diff_view(db, d) for d in
                    crud_allocation_diff.get_by_plan(db, plan_id)]
            has_changes = len(diffs) > 0
            remark = (
                "发现库存或供应承诺与冻结方案存在差异，已生成差异建议；"
                "已确认的分配结果保持不变，如需采纳请由计划员重新求解并确认新方案"
                if has_changes else "库存与供应承诺相较冻结方案无变化"
            )
            return AllocationRecheckResponse(
                plan_id=plan_id,
                plan_status=plan.status,
                has_changes=has_changes,
                diffs=view,
                remark=remark,
            )

    @staticmethod
    def _diff_view(db: Session, diff: AllocationDiff) -> AllocationDiffItem:
        batch_no = None
        if diff.production_batch_id:
            from app.models import ProductionBatch
            pb = db.query(ProductionBatch).filter(
                ProductionBatch.id == diff.production_batch_id
            ).first()
            batch_no = pb.batch_no if pb else None
        material_code = None
        if diff.material_id:
            mat = crud_material.get(db, diff.material_id)
            material_code = mat.code if mat else None
        return AllocationDiffItem(
            id=diff.id,
            production_batch_id=diff.production_batch_id,
            production_batch_no=batch_no,
            diff_type=diff.diff_type,
            source_type=diff.source_type,
            source_id=diff.source_id,
            material_id=diff.material_id,
            material_code=material_code,
            quantity_change=diff.quantity_change,
            detail=diff.detail,
            status=diff.status,
        )

    @staticmethod
    def resolve_diff(db: Session, diff_id: int, resolution: str) -> AllocationDiffItem:
        if resolution not in ("accepted", "ignored"):
            raise ValueError("差异处理结果只能是 accepted 或 ignored")
        diff = crud_allocation_diff.mark_resolved(db, diff_id, resolution)
        if not diff:
            raise ValueError(f"差异记录不存在: {diff_id}")
        return AllocationService._diff_view(db, diff)
