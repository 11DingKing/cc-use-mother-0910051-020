import pytest
from datetime import date, timedelta
from fastapi.testclient import TestClient

from tests.test_data_factory import DataFactory

from app.main import app
from app.services.allocation import AllocationService, AllocationConflictError
from app.services.inspection import InspectionService
from app.services.purchase import PurchaseService
from app.services.supplier_confirmation import SupplierConfirmationService
from app.crud.allocation import crud_allocation_plan, crud_allocation_diff_suggestion
from app.schemas import SupplierConfirmationConfirm


def _tm003_items(plan, factory):
    tm003_id = factory.materials["TM003"].id
    return [i for i in plan.items if i.material_id == tm003_id]


def _item_for(plan, factory, batch_no):
    batch_id = factory.production_batches[batch_no].id
    return next(i for i in plan.items if i.production_batch_id == batch_id)


class TestAllocationSolve:
    """替代料分配求解：有限库存真正分配到批次，不再重复承诺"""

    def test_limited_alternative_allocated_once_not_double_promised(self, db_session):
        """核心场景：两个批次同时缺主料，替代料只够一个批次。
        求解后只能有一个批次分到替代料，另一个落选并说明原因。"""
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()
        # TV001 的 TB001(50台,+10天)、TB002(30台,+30天) 都需要 TM003，主料无库存
        factory.create_alternative_material("TM003", "TM004", priority=1)
        factory.create_alternative_restriction("TM003_TM004", "TV001", is_allowed=True)
        factory.create_inventory_batch("TM004", quantity=50)  # 替代料只够 TB001

        plan = AllocationService.solve(
            db_session,
            material_ids=[factory.materials["TM003"].id],
            strategy="priority_first",
            created_by="计划员甲",
        )

        assert plan.status == "draft"
        tb001 = _item_for(plan, factory, "TB001")
        tb002 = _item_for(plan, factory, "TB002")

        # 交期早的 TB001 分到全部 50 件替代料
        assert tb001.status == "covered_by_alternative"
        assert tb001.allocated_alt_quantity == 50
        assert tb001.unmet_quantity == 0

        # TB002 落选，且说明原因
        assert tb002.status == "unmet"
        assert tb002.unmet_quantity == 30
        assert tb002.unmet_reason
        assert "替代料" in tb002.unmet_reason
        assert "TB001" in tb002.unmet_reason  # 说明库存被谁占用

        # 替代料总共只承诺了 50 件，没有重复分配
        total_alt = sum(i.allocated_alt_quantity for i in _tm003_items(plan, factory))
        assert total_alt == 50

        # 分配行指向具体库存批次
        alt_lines = [l for i in plan.items for l in i.lines if l.is_alternative]
        assert len(alt_lines) == 1
        assert alt_lines[0].material_id == factory.materials["TM004"].id
        assert alt_lines[0].inventory_batch_id is not None
        assert alt_lines[0].quantity == 50

    def test_vehicle_restriction_blocks_alternative(self, db_session):
        """车型不允许使用替代料时，批次落选并说明是车型限制"""
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()
        factory.create_alternative_material("TM003", "TM004", priority=1)
        factory.create_alternative_restriction("TM003_TM004", "TV001", is_allowed=False)
        factory.create_inventory_batch("TM004", quantity=500)

        plan = AllocationService.solve(
            db_session, material_ids=[factory.materials["TM003"].id]
        )

        tb001 = _item_for(plan, factory, "TB001")
        assert tb001.status == "unmet"
        assert "不允许使用替代料" in tb001.unmet_reason
        # 替代料库存一件都没有被分配
        assert sum(i.allocated_alt_quantity for i in _tm003_items(plan, factory)) == 0

    def test_substitution_ratio_and_percent(self, db_session):
        """替代比例：换算比例决定消耗多少替代料，百分比上限限制替代量"""
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()
        # 1件主料当量需要2件替代料；单批次最多替代50%
        factory.create_alternative_material(
            "TM003", "TM004", priority=1,
            substitution_ratio=2.0, max_substitution_percent=50
        )
        factory.create_alternative_restriction("TM003_TM004", "TV001", is_allowed=True)
        factory.create_inventory_batch("TM004", quantity=1000)

        plan = AllocationService.solve(
            db_session, material_ids=[factory.materials["TM003"].id]
        )

        # TB001 需求50：替代上限 50% = 25 当量，消耗 25*2=50 件 TM004
        tb001 = _item_for(plan, factory, "TB001")
        assert tb001.allocated_alt_quantity == 25
        assert tb001.unmet_quantity == 25
        assert tb001.status == "partial"
        assert "替代比例上限" in tb001.unmet_reason

        alt_line = next(l for l in tb001.lines if l.is_alternative)
        assert alt_line.quantity == 50  # 替代料自身单位
        assert alt_line.main_equivalent == 25  # 主料当量


class TestAllocationConfirmAndFreeze:
    """确认冻结、部分确认、取消与并发冲突"""

    def _solved_plan(self, db_session, factory, stock=100):
        factory.create_inventory_batch("TM003", quantity=stock)
        return AllocationService.solve(
            db_session, material_ids=[factory.materials["TM003"].id]
        )

    def test_confirm_freezes_and_inventory_change_only_suggests(self, db_session):
        """确认后冻结：库存变化只生成差异建议，不改写已确认结果"""
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()
        plan = self._solved_plan(db_session, factory, stock=100)

        plan = AllocationService.confirm(
            db_session, plan.id, version=1, confirmed_by="计划员甲"
        )
        assert plan.status == "confirmed"
        tb001 = _item_for(plan, factory, "TB001")
        assert tb001.item_status == "confirmed"
        confirmed_lines_before = {
            l.id: (l.quantity, l.status) for l in tb001.lines
        }

        # 库存被领用 80 件，物理库存只剩 20，低于已承诺的 80
        assert InspectionService.consume_material(
            db_session, factory.materials["TM003"].id, 80
        ) is True

        # 已确认结果不变（冻结）
        db_session.expire_all()
        plan_after = crud_allocation_plan.get(db_session, plan.id)
        tb001_after = _item_for(plan_after, factory, "TB001")
        assert tb001_after.item_status == "confirmed"
        for l in tb001_after.lines:
            assert (l.quantity, l.status) == confirmed_lines_before[l.id]

        # 但生成了差异建议
        suggestions = crud_allocation_diff_suggestion.get_by_plan(db_session, plan.id)
        assert len(suggestions) == 1
        assert suggestions[0].change_type == "inventory_changed"
        assert suggestions[0].status == "open"
        assert "重新求解" in suggestions[0].description

    def test_partial_confirm(self, db_session):
        """部分确认：只确认选中的明细，其余保持草稿可再次确认"""
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()
        plan = self._solved_plan(db_session, factory, stock=100)
        tb001 = _item_for(plan, factory, "TB001")
        tb002 = _item_for(plan, factory, "TB002")

        plan = AllocationService.confirm(
            db_session, plan.id, version=1,
            confirmed_by="计划员甲", item_ids=[tb001.id]
        )
        assert plan.status == "partially_confirmed"
        assert _item_for(plan, factory, "TB001").item_status == "confirmed"
        assert _item_for(plan, factory, "TB002").item_status == "draft"
        assert plan.version == 2

        # 剩余明细可再次确认
        plan = AllocationService.confirm(
            db_session, plan.id, version=2, confirmed_by="计划员甲"
        )
        assert plan.status == "confirmed"
        assert _item_for(plan, factory, "TB002").item_status == "confirmed"

    def test_version_conflict(self, db_session):
        """版本不匹配的确认被拒绝"""
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()
        plan = self._solved_plan(db_session, factory)

        with pytest.raises(AllocationConflictError):
            AllocationService.confirm(db_session, plan.id, version=99)

    def test_concurrent_planners_no_double_promise(self, db_session):
        """两个计划员并发求解同一库存：先确认者成功，后确认者冲突，库存不被重复承诺"""
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()
        factory.create_inventory_batch("TM003", quantity=50)  # 只够 TB001

        plan_a = AllocationService.solve(
            db_session, material_ids=[factory.materials["TM003"].id],
            created_by="计划员甲"
        )
        plan_b = AllocationService.solve(
            db_session, material_ids=[factory.materials["TM003"].id],
            created_by="计划员乙"
        )
        # 两个草稿都引用了同 50 件库存
        assert _item_for(plan_a, factory, "TB001").status == "covered"
        assert _item_for(plan_b, factory, "TB001").status == "covered"

        # 计划员甲先确认成功
        confirmed_a = AllocationService.confirm(
            db_session, plan_a.id, version=1, confirmed_by="计划员甲"
        )
        assert confirmed_a.status == "confirmed"

        # 计划员乙确认时库存已被承诺，冲突失败
        with pytest.raises(AllocationConflictError):
            AllocationService.confirm(
                db_session, plan_b.id, version=1, confirmed_by="计划员乙"
            )

        # 取消甲的方案后释放库存，乙的方案可以确认
        AllocationService.cancel(db_session, plan_a.id)
        confirmed_b = AllocationService.confirm(
            db_session, plan_b.id, version=1, confirmed_by="计划员乙"
        )
        assert confirmed_b.status == "confirmed"

    def test_cancel_releases_reservation(self, db_session):
        """取消方案释放库存承诺，新求解的方案可以分到库存"""
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()
        plan = self._solved_plan(db_session, factory, stock=50)
        plan = AllocationService.confirm(db_session, plan.id, version=1)

        cancelled = AllocationService.cancel(db_session, plan.id)
        assert cancelled.status == "cancelled"
        assert all(i.item_status == "cancelled" for i in cancelled.items)

        # 重新求解：库存不再被已取消方案占用
        new_plan = AllocationService.solve(
            db_session, material_ids=[factory.materials["TM003"].id]
        )
        assert _item_for(new_plan, factory, "TB001").status == "covered"


class TestSupplyPromiseUpdate:
    """供应承诺更新只生成差异建议"""

    def test_supply_promise_update_generates_suggestion(self, db_session):
        """在途订单被确认进方案后，供应承诺变化生成差异建议而非改写"""
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()
        # TM003 在途 50 件，+5天到货，可覆盖 TB001(+10天)
        factory.create_purchase_order(
            "PO-ALLOC-001", "TS002", "TM003",
            quantity=50, expected_date=date.today() + timedelta(days=5)
        )

        plan = AllocationService.solve(
            db_session, material_ids=[factory.materials["TM003"].id]
        )
        tb001 = _item_for(plan, factory, "TB001")
        assert tb001.status == "covered"
        assert any(l.source_type == "in_transit" for l in tb001.lines)

        plan = AllocationService.confirm(db_session, plan.id, version=1)
        assert plan.status == "confirmed"

        # 在途订单全部到货给了别人（在途剩余归零）
        factory.create_delivery(
            "DEL-ALLOC-001", "PO-ALLOC-001", "TS002", "TM003", quantity=50
        )

        # 供应商确认（供应承诺更新）触发校验
        suggestions = PurchaseService.generate_purchase_suggestions(db_session)
        tm003_suggestion = next(
            s for s in suggestions
            if s.material and s.material.code == "TM003"
        )
        confirmation = SupplierConfirmationService.create_confirmation_from_suggestion(
            db_session, suggestion_id=tm003_suggestion.id,
            confirmation_no="CONF-ALLOC-001",
            supplier_id=factory.suppliers["TS002"].id
        )
        SupplierConfirmationService.supplier_confirm(
            db_session, confirmation.id,
            SupplierConfirmationConfirm(committed_quantity=50, batches=[])
        )

        diffs = crud_allocation_diff_suggestion.get_by_plan(db_session, plan.id)
        assert any(d.change_type == "supply_promise_updated" for d in diffs)

        # 已确认的在途分配行不变
        db_session.expire_all()
        plan_after = crud_allocation_plan.get(db_session, plan.id)
        tb001_after = _item_for(plan_after, factory, "TB001")
        assert tb001_after.item_status == "confirmed"
        assert all(l.status == "confirmed" for l in tb001_after.lines)


class TestStrategyCompare:
    """策略对比：说明改用其他策略会影响哪些交期"""

    def test_compare_shows_delivery_diffs(self, db_session):
        """优先级优先与交期优先对同一库存给出不同分配，差异可解释"""
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()
        # TV002 也使用 TM003：TB003(200台,+5天,优先级8) 与 TB001(50台,+10天,优先级10) 抢料
        factory.add_bom_item("TV002", "TM003", 1)
        factory.create_inventory_batch("TM003", quantity=100)

        result = AllocationService.compare_strategies(
            db_session, material_ids=[factory.materials["TM003"].id]
        )

        assert len(result.strategies) == 3
        by_name = {s.strategy: s for s in result.strategies}

        tb001_id = factory.production_batches["TB001"].id
        tb003_id = factory.production_batches["TB003"].id

        def outcome(strategy, batch_id):
            return next(
                o for o in by_name[strategy].outcomes
                if o.production_batch_id == batch_id
            )

        # 优先级优先：TB001(优先级10) 先分到 50 件，按期
        assert outcome("priority_first", tb001_id).status == "covered"
        # 交期优先：TB003(交期+5天) 先拿走 100 件，TB001 无料落选
        assert outcome("deadline_first", tb001_id).unmet_quantity == 50
        assert outcome("deadline_first", tb003_id).unmet_quantity == 100

        # 差异列表指出哪些批次交期受策略影响
        assert len(result.delivery_diffs) > 0
        tb001_diff = next(
            d for d in result.delivery_diffs if d.production_batch_id == tb001_id
        )
        assert tb001_diff.by_strategy["priority_first"]["status"] == "covered"
        assert tb001_diff.by_strategy["deadline_first"]["unmet_quantity"] == 50
        assert tb001_diff.best_strategy == "priority_first"
        assert tb001_diff.worst_strategy in ("deadline_first", "fair_share")


class TestAllocationAPI:
    """接口层：求解、确认、冲突码、对比"""

    def test_solve_confirm_and_conflict_api(self, db_session, override_get_db):
        client = TestClient(app)
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()
        factory.create_inventory_batch("TM003", quantity=50)
        tm003_id = factory.materials["TM003"].id

        # 两个计划员分别求解
        plan_ids = []
        for planner in ("计划员甲", "计划员乙"):
            resp = client.post("/api/v1/allocations/solve", json={
                "material_ids": [tm003_id],
                "strategy": "priority_first",
                "created_by": planner,
            })
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "draft"
            assert body["version"] == 1
            assert len(body["items"]) > 0
            plan_ids.append((body["id"], body["version"]))

        # 先确认的成功
        resp = client.post(
            f"/api/v1/allocations/{plan_ids[0][0]}/confirm",
            json={"version": plan_ids[0][1], "confirmed_by": "计划员甲"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "confirmed"

        # 后确认的 409：同一库存不被重复承诺
        resp = client.post(
            f"/api/v1/allocations/{plan_ids[1][0]}/confirm",
            json={"version": plan_ids[1][1], "confirmed_by": "计划员乙"},
        )
        assert resp.status_code == 409

    def test_version_conflict_returns_409(self, db_session, override_get_db):
        client = TestClient(app)
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()
        factory.create_inventory_batch("TM003", quantity=50)
        tm003_id = factory.materials["TM003"].id

        resp = client.post("/api/v1/allocations/solve", json={
            "material_ids": [tm003_id], "strategy": "deadline_first"
        })
        plan_id = resp.json()["id"]

        resp = client.post(
            f"/api/v1/allocations/{plan_id}/confirm", json={"version": 99}
        )
        assert resp.status_code == 409

    def test_compare_api(self, db_session, override_get_db):
        client = TestClient(app)
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()
        factory.add_bom_item("TV002", "TM003", 1)
        factory.create_inventory_batch("TM003", quantity=100)

        resp = client.post("/api/v1/allocations/compare", json={
            "material_ids": [factory.materials["TM003"].id]
        })
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["strategies"]) == 3
        assert len(body["delivery_diffs"]) > 0

    def test_diff_suggestions_api(self, db_session, override_get_db):
        client = TestClient(app)
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()
        factory.create_inventory_batch("TM003", quantity=100)
        tm003_id = factory.materials["TM003"].id

        resp = client.post("/api/v1/allocations/solve", json={
            "material_ids": [tm003_id]
        })
        plan = resp.json()
        resp = client.post(
            f"/api/v1/allocations/{plan['id']}/confirm",
            json={"version": plan["version"]},
        )
        assert resp.status_code == 200

        # 库存变化触发差异建议
        assert InspectionService.consume_material(db_session, tm003_id, 90) is True

        resp = client.get(f"/api/v1/allocations/{plan['id']}/diff-suggestions")
        assert resp.status_code == 200
        suggestions = resp.json()
        assert len(suggestions) == 1
        assert suggestions[0]["change_type"] == "inventory_changed"

        # 忽略建议
        resp = client.post(
            f"/api/v1/allocations/diff-suggestions/{suggestions[0]['id']}/dismiss"
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "dismissed"
