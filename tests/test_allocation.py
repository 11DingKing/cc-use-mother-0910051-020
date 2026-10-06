import threading
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.test_data_factory import DataFactory
from app.database import Base
from app.services.allocation import AllocationService
from app.services.delay_analysis import DelayAnalysisService
from app.schemas import AllocationSolveRequest, AllocationConfirmRequest, AllocationCancelRequest
from app.models import (
    SupplierConfirmation, SupplierConfirmationBatch,
    InventoryBatch, PurchaseOrder,
)
from app.crud.purchase import crud_inventory_batch


def _solve(factory, material_code, **kwargs):
    return AllocationService.solve(
        factory.db,
        AllocationSolveRequest(
            material_id=factory.materials[material_code].id,
            **kwargs,
        ),
    )


def _setup_competing_batches(factory, alt_stock, main_stock=0,
                             v1_allow=True, v2_allow=True,
                             v1_qty=100, v2_qty=100,
                             v1_date=None, v2_date=None,
                             v1_priority=10, v2_priority=5):
    """两个车型 V1/V2 各一个批次同时缺主料 TM001，TM002 为其替代料"""
    today = date.today()
    factory.create_material("TM001", "主料", "车架", "件")
    factory.create_material("TM002", "替代料", "车架", "件")
    factory.create_vehicle_model("TV001", "高优先车型", priority=v1_priority)
    factory.create_vehicle_model("TV002", "低优先车型", priority=v2_priority)
    factory.add_bom_item("TV001", "TM001", 1)
    factory.add_bom_item("TV002", "TM001", 1)
    factory.create_alternative_material("TM001", "TM002", priority=1)
    if not v1_allow:
        factory.create_alternative_restriction("TM001_TM002", "TV001", is_allowed=False)
    if not v2_allow:
        factory.create_alternative_restriction("TM001_TM002", "TV002", is_allowed=False)
    if main_stock:
        factory.create_inventory_batch("TM001", quantity=main_stock)
    if alt_stock:
        factory.create_inventory_batch("TM002", quantity=alt_stock)
    factory.create_production_batch(
        "TB001", "TV001", v1_qty, v1_date or today + timedelta(days=10))
    factory.create_production_batch(
        "TB002", "TV002", v2_qty, v2_date or today + timedelta(days=10))


class TestFiniteAllocation:
    """有限库存真正分配：多批次缺主料时不能重复承诺同一替代料"""

    def test_shared_alternative_not_double_promised(self, db_session):
        """两个批次共需200，替代料只有100：只能有一个批次按时满足"""
        factory = DataFactory(db_session)
        _setup_competing_batches(factory, alt_stock=100)

        resp = _solve(factory, "TM001", plan_no="AP-T1", compare_strategies=False)
        by_batch = {r.production_batch_no: r for r in resp.results}

        satisfied = [r for r in resp.results if r.on_time]
        assert len(satisfied) == 1, "替代料100只够一个100件批次，不能两批都判按期"
        winner = satisfied[0]
        loser = next(r for r in resp.results if not r.on_time)

        # 交期相同，高优先级车型 V001 应先分
        assert winner.production_batch_no == "TB001"
        assert winner.allocated_equivalent == 100
        assert loser.shortage_quantity == 100
        assert loser.status in ("delayed", "unfulfillable")
        # 落选说明必须指出替代料已被占用/不足
        assert any(
            ("不足" in x.get("reason", "")) or ("占用" in x.get("reason", ""))
            for x in loser.rejected_reasons
        )

        # 供应视图：替代料来源剩余为 0，持有 100，不能被再次承诺
        alt_source = next(
            s for s in resp.sources
            if s.source_material_code == "TM002"
        )
        assert alt_source.remaining_quantity == 0
        assert alt_source.held_quantity == 100

    def test_second_solve_reserves_first_plan_hold(self, db_session):
        """已有草稿方案占用后再次求解，第二个方案不能重复承诺同一库存"""
        factory = DataFactory(db_session)
        _setup_competing_batches(factory, alt_stock=100)

        r1 = _solve(factory, "TM001", plan_no="AP-T2A", compare_strategies=False)
        r2 = _solve(factory, "TM001",
                    production_batch_ids=[
                        factory.production_batches["TB002"].id
                    ],
                    plan_no="AP-T2B", compare_strategies=False)

        # 第一份方案已拿走全部替代料，第二份方案中的 TB002 无料可分
        assert any(r.on_time for r in r1.results)
        tb002 = next(r for r in r2.results if r.production_batch_no == "TB002")
        assert not tb002.on_time
        assert tb002.shortage_quantity == 100
        # 第二份方案带草稿占用提醒
        assert r2.warning and "草稿" in r2.warning

    def test_vehicle_restriction_blocks_alternative(self, db_session):
        """车型不允许使用替代料时，即使替代料有库存也不能分配"""
        factory = DataFactory(db_session)
        _setup_competing_batches(factory, alt_stock=200, v1_allow=False)

        resp = _solve(factory, "TM001", plan_no="AP-T3", compare_strategies=False)
        v1 = next(r for r in resp.results if r.production_batch_no == "TB001")
        v2 = next(r for r in resp.results if r.production_batch_no == "TB002")

        assert not v1.on_time
        assert any(
            x.get("reason") == "该车型不允许使用此替代料"
            for x in v1.rejected_reasons
        )
        # V002 允许使用，200 足够
        assert v2.on_time

    def test_main_material_allocated_first(self, db_session):
        """主料按时可用时优先用主料，不足部分才动替代料"""
        factory = DataFactory(db_session)
        _setup_competing_batches(factory, main_stock=60, alt_stock=100,
                                 v1_date=date.today() + timedelta(days=8))

        resp = _solve(factory, "TM001", plan_no="AP-T4", compare_strategies=False)
        tb001 = next(r for r in resp.results if r.production_batch_no == "TB001")
        assert tb001.on_time
        main_qty = sum(
            s["allocated_quantity"]
            for s in tb001.sources if s["material_code"] == "TM001"
        )
        alt_qty = sum(
            s["allocated_quantity"]
            for s in tb001.sources if s["material_code"] == "TM002"
        )
        assert main_qty == 60
        assert alt_qty == 40

    def test_in_transit_after_plan_date_cannot_cover_on_time(self, db_session):
        """交期之后才到货的在途订单不能让批次按时完成，但可给出延期日期"""
        factory = DataFactory(db_session)
        today = date.today()
        _setup_competing_batches(
            factory, alt_stock=0,
            v1_date=today + timedelta(days=5), v2_date=today + timedelta(days=5))
        factory.create_supplier("TS001", "测试供应商")
        factory.create_purchase_order(
            "PO-T5", "TS001", "TM001", 200,
            expected_date=today + timedelta(days=20))

        resp = _solve(factory, "TM001",
                      plan_no="AP-T5",
                      include_supplier_commitments=False,
                      compare_strategies=False)
        tb001 = next(r for r in resp.results if r.production_batch_no == "TB001")
        assert not tb001.on_time
        assert tb001.status == "delayed"
        assert tb001.fulfillable_date == today + timedelta(days=20)


class TestRatios:
    """替代换算比例与最大替代比例"""

    def test_substitution_ratio_reduces_equivalent(self, db_session):
        """1单位替代料仅折合0.5主料：100替代料只能抵50主料"""
        factory = DataFactory(db_session)
        _setup_competing_batches(factory, alt_stock=100,
                                 v1_qty=100, v2_qty=0)
        alt = factory.alternatives["TM001_TM002"]
        from app.crud.allocation import crud_alternative_ratio
        from app.schemas import AlternativeRatioCreate
        crud_alternative_ratio.create(db_session, obj_in=AlternativeRatioCreate(
            alternative_id=alt.id, substitution_ratio=0.5))

        resp = _solve(factory, "TM001",
                      production_batch_ids=[factory.production_batches["TB001"].id],
                      plan_no="AP-R1", compare_strategies=False)
        r = resp.results[0]
        assert r.allocated_equivalent == 50
        assert r.shortage_quantity == 50

    def test_max_share_caps_alternative(self, db_session):
        """最大替代比例50%：即便替代料充足，一个批次最多用50%"""
        factory = DataFactory(db_session)
        _setup_competing_batches(factory, alt_stock=1000,
                                 v1_qty=100, v2_qty=0)
        alt = factory.alternatives["TM001_TM002"]
        from app.crud.allocation import crud_alternative_ratio
        from app.schemas import AlternativeRatioCreate
        crud_alternative_ratio.create(db_session, obj_in=AlternativeRatioCreate(
            alternative_id=alt.id, substitution_ratio=1.0, max_share=0.5))

        resp = _solve(factory, "TM001",
                      production_batch_ids=[factory.production_batches["TB001"].id],
                      plan_no="AP-R2", compare_strategies=False)
        r = resp.results[0]
        alt_equiv = sum(
            s["equivalent_quantity"] for s in r.sources
            if s["material_code"] == "TM002"
        )
        assert alt_equiv == 50
        assert r.shortage_quantity == 50


class TestStrategyComparison:
    def test_strategy_comparison_shows_affected_batches(self, db_session):
        """交期优先与优先级优先结果不同，对比结果要点名受影响批次与交期"""
        factory = DataFactory(db_session)
        today = date.today()
        # 高优先级但交期晚；低优先级但交期早；替代料仅够一个批次
        _setup_competing_batches(
            factory, alt_stock=100,
            v1_qty=100, v2_qty=100,
            v1_date=today + timedelta(days=20),
            v2_date=today + timedelta(days=3),
        )

        resp = _solve(factory, "TM001", strategy="due_date", plan_no="AP-S1")
        comp = resp.strategy_comparison
        assert comp is not None
        assert comp.compared_strategy == "priority_first"
        # 三种策略汇总齐全
        assert len(comp.summaries) == 3
        # 交期优先下早交期的 TB002 满足；对比优先级绝对优先时 TB002 落选
        due_winner = next(
            r for r in resp.results if r.production_batch_no == "TB002")
        assert due_winner.on_time
        impacted = {i.production_batch_no for i in comp.impacts}
        assert "TB002" in impacted or "TB001" in impacted


class TestFreezeAndDiffs:
    def _freeze(self, factory, plan_no="AP-F1", **kwargs):
        resp = _solve(factory, "TM001", plan_no=plan_no,
                      compare_strategies=False, **kwargs)
        plan = AllocationService.confirm(
            factory.db, resp.plan.id,
            confirmed_by="计划员甲",
        )
        return resp, plan

    def test_confirm_freezes_and_inventory_change_only_suggests_diff(self, db_session):
        factory = DataFactory(db_session)
        _setup_competing_batches(factory, main_stock=100, alt_stock=0)
        resp, plan = self._freeze(factory)
        assert plan.status == "frozen"
        assert plan.confirmed_by == "计划员甲"

        # 冻结后库存骤降：只能出差异建议，方案明细不变
        inv = db_session.query(InventoryBatch).filter(
            InventoryBatch.material_id == factory.materials["TM001"].id
        ).first()
        inv.available_quantity = 10
        db_session.commit()

        recheck = AllocationService.recheck(db_session, plan.id)
        assert recheck.has_changes
        diff_types = {d.diff_type for d in recheck.diffs}
        assert "source_reduced" in diff_types
        reduced = next(d for d in recheck.diffs if d.diff_type == "source_reduced")
        assert reduced.quantity_change == -90
        assert reduced.production_batch_no == "TB001"
        # 冻结明细原样保留
        detail = AllocationService.get_plan_detail(db_session, plan.id)
        frozen_total = sum(i.allocated_quantity for i in detail.items)
        assert frozen_total == 100

    def test_new_supply_creates_supply_added_diff_without_rewrite(self, db_session):
        factory = DataFactory(db_session)
        _setup_competing_batches(factory, main_stock=60, alt_stock=0)
        _, plan = self._freeze(factory, plan_no="AP-F2")

        # 主料新增到货入库（新批次）
        factory.create_inventory_batch("TM001", quantity=500, location="合格区-NEW")
        recheck = AllocationService.recheck(db_session, plan.id)
        added = [d for d in recheck.diffs if d.diff_type == "supply_added"]
        assert added and all(d.quantity_change == 500 for d in added)
        # 方案本身不被改写
        detail = AllocationService.get_plan_detail(db_session, plan.id)
        assert sum(i.allocated_quantity for i in detail.items) == 60

    def test_supplier_commitment_date_change_is_diff(self, db_session):
        factory = DataFactory(db_session)
        today = date.today()
        _setup_competing_batches(
            factory, alt_stock=0,
            v1_date=today + timedelta(days=5),
            v2_date=today + timedelta(days=5))
        # 直接造一条已确认供应商承诺分批作为供应来源
        conf = SupplierConfirmation(
            confirmation_no="CONF-T", purchase_suggestion_id=0,
            supplier_id=0, material_id=factory.materials["TM001"].id,
            requested_quantity=200, committed_quantity=200,
            status="confirmed",
        )
        db_session.add(conf)
        db_session.commit()
        cb = SupplierConfirmationBatch(
            confirmation_id=conf.id, batch_no="CB-T",
            quantity=200, planned_date=today + timedelta(days=4))
        db_session.add(cb)
        db_session.commit()

        resp = _solve(factory, "TM001", plan_no="AP-F3", compare_strategies=False)
        assert all(r.on_time for r in resp.results)
        plan = AllocationService.confirm(db_session, resp.plan.id, confirmed_by="甲")

        cb.planned_date = today + timedelta(days=18)
        db_session.commit()
        recheck = AllocationService.recheck(db_session, plan.id)
        assert any(d.diff_type == "date_changed" for d in recheck.diffs)

    def test_partial_confirmation_releases_rejected_batches(self, db_session):
        factory = DataFactory(db_session)
        _setup_competing_batches(factory, alt_stock=100)
        resp = _solve(factory, "TM001", plan_no="AP-F4", compare_strategies=False)
        winner_id = next(r.production_batch_id for r in resp.results if r.on_time)
        loser_id = next(r.production_batch_id for r in resp.results if not r.on_time)

        # 计划员只接受原本落选的批次（拒绝获胜批次），其库存占用应被释放
        plan = AllocationService.confirm(
            db_session, resp.plan.id,
            confirmed_by="计划员乙",
            accepted_batch_ids=[loser_id],
        )
        detail = AllocationService.get_plan_detail(db_session, plan.id)
        # 落选批次本就无分配明细；获胜批次的预留明细必须已删除（库存释放）
        assert all(i.production_batch_id != winner_id for i in detail.items)
        winner_snapshot = next(
            r for r in detail.results if r.production_batch_id == winner_id)
        assert winner_snapshot.status == "rejected_in_confirmation"

        # 获胜批次被释放后可重新求解，拿到100替代料按时生产
        resp2 = _solve(factory, "TM001",
                       production_batch_ids=[winner_id],
                       plan_no="AP-F4B", compare_strategies=False)
        assert all(r.on_time for r in resp2.results)

    def test_cancel_releases_holds(self, db_session):
        factory = DataFactory(db_session)
        _setup_competing_batches(factory, alt_stock=100)
        resp = _solve(factory, "TM001", plan_no="AP-C1", compare_strategies=False)
        AllocationService.cancel(db_session, resp.plan.id, cancelled_by="甲",
                                 reason="需求取消")
        # 取消后重新求解，两个批次恢复到"第一个满足"的原始竞争状态
        resp2 = _solve(factory, "TM001", plan_no="AP-C2", compare_strategies=False)
        assert sum(1 for r in resp2.results if r.on_time) == 1

    def test_confirm_blocked_when_inventory_shrunk(self, db_session):
        """求解后库存被其他业务挪用，冻结时必须拦截而不是超额承诺"""
        factory = DataFactory(db_session)
        _setup_competing_batches(factory, alt_stock=100)
        resp = _solve(factory, "TM001", plan_no="AP-F5", compare_strategies=False)

        inv = db_session.query(InventoryBatch).filter(
            InventoryBatch.material_id == factory.materials["TM002"].id
        ).first()
        inv.available_quantity = 0
        db_session.commit()

        with pytest.raises(ValueError, match="重新求解"):
            AllocationService.confirm(db_session, resp.plan.id, confirmed_by="甲")

    def test_double_confirm_rejected(self, db_session):
        factory = DataFactory(db_session)
        _setup_competing_batches(factory, alt_stock=100)
        resp = _solve(factory, "TM001", plan_no="AP-F6", compare_strategies=False)
        AllocationService.confirm(db_session, resp.plan.id, confirmed_by="甲")
        with pytest.raises(ValueError, match="draft"):
            AllocationService.confirm(db_session, resp.plan.id, confirmed_by="乙")


class TestConcurrentSolves:
    def test_two_planners_concurrent_no_double_commit(self, tmp_path):
        """两个计划员并发求解同一缺料场景：跨方案的库存承诺总量不得超过供应量"""
        db_path = tmp_path / "concurrent.db"
        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(bind=engine, autoflush=False)

        # 主线程准备数据
        setup_db = SessionLocal()
        factory = DataFactory(setup_db)
        _setup_competing_batches(factory, alt_stock=100)
        mat_id = factory.materials["TM001"].id
        b1 = factory.production_batches["TB001"].id
        b2 = factory.production_batches["TB002"].id
        setup_db.close()

        errors = []

        def planner(batch_id, plan_no):
            session = SessionLocal()
            try:
                AllocationService.solve(session, AllocationSolveRequest(
                    material_id=mat_id,
                    production_batch_ids=[batch_id],
                    plan_no=plan_no,
                    compare_strategies=False,
                    created_by="计划员",
                ))
            except Exception as e:  # noqa: BLE001
                errors.append(e)
            finally:
                session.close()

        t1 = threading.Thread(target=planner, args=(b1, "AP-CC1"))
        t2 = threading.Thread(target=planner, args=(b2, "AP-CC2"))
        t1.start(); t2.start()
        t1.join(); t2.join()
        assert not errors

        check = SessionLocal()
        from app.models import AllocationPlan, AllocationPlanItem
        items = (
            check.query(AllocationPlanItem)
            .join(AllocationPlan, AllocationPlan.id == AllocationPlanItem.plan_id)
            .filter(AllocationPlan.status == "draft")
            .all()
        )
        alt_material_id = None
        factory2 = DataFactory(check)
        # 直接按 source_type=inventory 汇总替代料批次占用
        alt_batches = check.query(InventoryBatch).filter(
            InventoryBatch.material_id != mat_id
        ).all()
        alt_total = sum(b.available_quantity for b in alt_batches)
        held = sum(
            i.allocated_quantity for i in items
            if i.source_material_id in {b.material_id for b in alt_batches}
        )
        assert held <= alt_total == 100, "并发求解后替代料被重复承诺"
        check.close()


class TestDelayAnalysisRegression:
    def test_delay_analysis_consumes_alternative_once(self, db_session):
        """回归：延期分析中两个批次不能把同一份替代料库存各用一遍"""
        factory = DataFactory(db_session)
        factory.create_material("TM001", "主料", "车架", "件")
        factory.create_material("TM002", "替代料", "车架", "件")
        factory.create_vehicle_model("TV001", "车型一", priority=10)
        factory.create_vehicle_model("TV002", "车型二", priority=5)
        factory.add_bom_item("TV001", "TM001", 1)
        factory.add_bom_item("TV002", "TM001", 1)
        factory.create_alternative_material("TM001", "TM002", priority=1)
        factory.create_inventory_batch("TM002", quantity=100)
        factory.create_supplier("TS001", "供应商")
        today = date.today()
        factory.create_production_batch("TB001", "TV001", 100, today + timedelta(days=5))
        factory.create_production_batch("TB002", "TV002", 100, today + timedelta(days=5))
        po = factory.create_purchase_order(
            "PO-REG1", "TS001", "TM001", 200,
            expected_date=today + timedelta(days=3), status="ordered")

        result = DelayAnalysisService.analyze_delay_impact(
            db_session, purchase_order_id=po.id, delay_days=30)

        details = {d["batch_no"]: d for d in result.analysis_details}
        statuses = [d["status"] for d in details.values()]
        # 一个批次可用替代料覆盖，另一个必须暴露为延期/不足，不能两个都是替代覆盖
        assert statuses.count("covered_by_alternative") <= 1
        assert any(s in ("delayed", "insufficient_supply") for s in statuses)
