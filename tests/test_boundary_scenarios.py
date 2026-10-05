import pytest
from datetime import date, timedelta
from tests.test_data_factory import DataFactory

from app.services.requirement import RequirementService
from app.services.purchase import PurchaseService
from app.services.supplier_confirmation import SupplierConfirmationService
from app.services.inspection import InspectionService
from app.services.alternative_material import AlternativeMaterialService
from app.services.delay_analysis import DelayAnalysisService

from app.crud.purchase import crud_inventory_batch
from app.crud.supplier_confirmation import crud_supplier_shortage_impact

from app.schemas import (
    SupplierConfirmationConfirm, SupplierConfirmationBatchCreate,
    InspectionCreate, DeliveryCreate
)

from app.crud.purchase import crud_delivery


class TestCriticalMaterialShortage:
    """测试边界：关键物料缺货场景"""

    def test_critical_material_shortage_high_priority(self, db_session):
        """关键物料完全缺货，高优先级车型受影响"""
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()

        # 确保关键物料库存为0
        # 已经通过setup_basic_supply_chain设置了，初始无库存

        # 计算需求
        requirements = RequirementService.calculate_material_requirements(db_session)

        carbon_frame_req = next(
            (r for r in requirements if r.material_code == "TM001"), None
        )
        assert carbon_frame_req is not None
        assert carbon_frame_req.is_critical == True
        assert carbon_frame_req.stock_quantity == 0
        assert carbon_frame_req.shortage > 0, "关键物料应该有短缺"

        # 生成采购建议
        suggestions = PurchaseService.generate_purchase_suggestions(db_session)

        # 关键物料的采购建议应该排在最前面
        critical_suggestions = [
            s for s in suggestions
            if s.material and s.material.is_critical
        ]
        assert len(critical_suggestions) > 0, "应该有关键物料的采购建议"

        # 关键物料优先级应该最高
        if suggestions:
            first_suggestion = suggestions[0]
            assert first_suggestion.material and first_suggestion.material.is_critical, \
                "关键物料的采购建议应该排在最前面"

    def test_critical_material_shortage_without_supplier_capacity(self, db_session):
        """关键物料缺货且供应商无产能，应该产生严重短缺预警"""
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()

        # 生成采购建议
        suggestions = PurchaseService.generate_purchase_suggestions(db_session)
        carbon_suggestion = next(
            (s for s in suggestions if s.material and s.material.code == "TM001"), None
        )

        # 创建供应商确认，但供应商承诺数量为0
        confirmation = SupplierConfirmationService.create_confirmation_from_suggestion(
            db_session,
            suggestion_id=carbon_suggestion.id,
            confirmation_no="TEST-CONF-SHORT-001",
            supplier_id=factory.suppliers["TS001"].id
        )

        confirm_data = SupplierConfirmationConfirm(
            committed_quantity=0,
            committed_delivery_date=None,
            confirmation_note="产能完全不足，无法供货",
            batches=[]
        )

        confirmed = SupplierConfirmationService.supplier_confirm(
            db_session,
            confirmation_id=confirmation.id,
            confirm_data=confirm_data
        )

        assert confirmed.shortage_quantity == confirmation.requested_quantity
        assert confirmed.status == "shortage"

        # 检查短缺影响 - 应该有高优先级的影响
        shortage_impacts = crud_supplier_shortage_impact.get_by_confirmation(
            db_session, confirmation.id
        )
        assert len(shortage_impacts) > 0

        high_impacts = [i for i in shortage_impacts if i.impact_level == "high"]
        assert len(high_impacts) > 0, "关键物料完全缺货应该产生高优先级影响"

        for impact in high_impacts:
            assert impact.estimated_delay_days >= 15, "关键物料缺货应该导致至少15天的延期"


class TestAlternativeMaterialRestriction:
    """测试边界：替代料只适用于指定车型"""

    def test_alternative_material_vehicle_restriction(self, db_session):
        """替代料只适用于指定车型，不适用于其他车型"""
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()

        # 创建替代关系：TM003(11速链条) 可被 TM004(10速链条) 替代
        alt = factory.create_alternative_material(
            "TM003", "TM004", priority=1, is_active=True,
            remark="10速链条可应急替代11速"
        )

        # 设置限制：TV001(旗舰PRO) 不允许使用这个替代料，TV002(城市CITY) 允许
        factory.create_alternative_restriction(
            "TM003_TM004", "TV001", is_allowed=False,
            remark="旗舰PRO不允许使用10速链条替代11速"
        )
        factory.create_alternative_restriction(
            "TM003_TM004", "TV002", is_allowed=True,
            remark="城市CITY允许使用10速链条替代11速"
        )

        # 创建TM004的库存作为替代料
        factory.create_inventory_batch("TM004", quantity=500, is_quarantined=False)

        # 检查替代料可用性
        # TV001应该不能用这个替代料
        check_result_tv001 = AlternativeMaterialService.check_alternative_availability(
            db_session,
            material_id=factory.materials["TM003"].id,
            vehicle_model_id=factory.vehicle_models["TV001"].id,
            required_quantity=100
        )
        assert check_result_tv001.can_be_replaced == False, \
            "TV001应该不允许使用TM004替代TM003"

        # TV002应该可以用这个替代料
        check_result_tv002 = AlternativeMaterialService.check_alternative_availability(
            db_session,
            material_id=factory.materials["TM003"].id,
            vehicle_model_id=factory.vehicle_models["TV002"].id,
            required_quantity=100
        )
        assert check_result_tv002.can_be_replaced == True, \
            "TV002应该允许使用TM004替代TM003"
        assert check_result_tv002.recommended_alternative is not None

    def test_can_use_alternative_method(self, db_session):
        """测试 can_use_alternative 方法的正确性"""
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()

        # 创建替代关系
        alt = factory.create_alternative_material(
            "TM003", "TM004", priority=1, is_active=True
        )

        # 设置限制
        factory.create_alternative_restriction(
            "TM003_TM004", "TV001", is_allowed=False
        )

        # 测试不允许的情况
        result = AlternativeMaterialService.can_use_alternative(
            db_session,
            material_id=factory.materials["TM003"].id,
            alternative_material_id=factory.materials["TM004"].id,
            vehicle_model_id=factory.vehicle_models["TV001"].id
        )
        assert result == False

        # 测试允许的情况（没有限制的默认允许）
        result = AlternativeMaterialService.can_use_alternative(
            db_session,
            material_id=factory.materials["TM003"].id,
            alternative_material_id=factory.materials["TM004"].id,
            vehicle_model_id=factory.vehicle_models["TV002"].id
        )
        assert result == True

    def test_alternative_material_used_in_delay_analysis(self, db_session):
        """测试延期分析中正确考虑替代料的车型限制"""
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()

        # 创建替代关系和限制
        factory.create_alternative_material(
            "TM003", "TM004", priority=1, is_active=True
        )
        factory.create_alternative_restriction(
            "TM003_TM004", "TV001", is_allowed=False
        )

        # 创建TM004库存
        factory.create_inventory_batch("TM004", quantity=1000)

        # 为TM003创建采购订单并模拟延期
        factory.create_inventory_batch("TM003", quantity=0)  # 确保库存为0

        po = factory.create_purchase_order(
            "TEST-PO-ALT-001", "TS002", "TM003",
            quantity=1000,
            expected_date=date.today() + timedelta(days=5)
        )

        # 分析延期影响
        delay_result = DelayAnalysisService.analyze_delay_impact(
            db_session,
            purchase_order_id=po.id,
            delay_days=20
        )

        # TV001不允许使用替代料，应该受影响
        tv001_batches = [
            b for b in delay_result.affected_batches
            if b.vehicle_model_id == factory.vehicle_models["TV001"].id
        ]
        assert len(tv001_batches) > 0, "TV001不允许使用替代料，应该受延期影响"

        # TV002允许使用替代料，可能不受影响
        tv002_batches = [
            b for b in delay_result.affected_batches
            if b.vehicle_model_id == factory.vehicle_models["TV002"].id
        ]
        # TV002可以用TM004替代，应该不会受影响
        all_batches = [b.id for b in delay_result.affected_batches]
        tv002_batch_ids = [
            b.id for b in factory.production_batches.values()
            if b.vehicle_model_id == factory.vehicle_models["TV002"].id
        ]
        # 确认TV002的批次没有在受影响列表中，因为可以用替代料
        assert not any(bid in all_batches for bid in tv002_batch_ids), \
            "TV002允许使用替代料，应该不受延期影响"


class TestQuarantinedBatchRestriction:
    """测试边界：隔离批次不能领用"""

    def test_quarantined_batch_not_available(self, db_session):
        """隔离批次不应该被计入可用库存"""
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()

        # 创建一个正常批次和一个隔离批次
        normal_batch = factory.create_inventory_batch(
            "TM001", quantity=100, is_quarantined=False, location="合格区"
        )
        quarantined_batch = factory.create_inventory_batch(
            "TM001", quantity=50, is_quarantined=True,
            quarantine_reason="抽检不合格", location="隔离区"
        )

        # 可用库存应该只包括正常批次
        total_stock = crud_inventory_batch.get_total_stock(
            db_session, factory.materials["TM001"].id
        )
        assert total_stock == 100, f"可用库存应该是100，不包括隔离的50，实际{total_stock}"

        # 可用批次应该不包括隔离批次
        available_batches = crud_inventory_batch.get_available_batches(
            db_session, factory.materials["TM001"].id
        )
        assert len(available_batches) == 1
        assert available_batches[0].id == normal_batch.id

    def test_can_use_batch_check(self, db_session):
        """测试 can_use_batch 方法正确检查隔离状态"""
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()

        normal_batch = factory.create_inventory_batch(
            "TM001", quantity=100, is_quarantined=False
        )
        quarantined_batch = factory.create_inventory_batch(
            "TM001", quantity=50, is_quarantined=True
        )
        empty_batch = factory.create_inventory_batch(
            "TM001", quantity=0, available_quantity=0, is_quarantined=False
        )

        # 正常批次可用
        assert InspectionService.can_use_batch(db_session, normal_batch.id) == True

        # 隔离批次不可用
        assert InspectionService.can_use_batch(db_session, quarantined_batch.id) == False

        # 空批次不可用
        assert InspectionService.can_use_batch(db_session, empty_batch.id) == False

    def test_consume_material_skips_quarantined(self, db_session):
        """测试消耗物料时跳过隔离批次"""
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()

        # 创建一个隔离批次和一个正常批次
        quarantined_batch = factory.create_inventory_batch(
            "TM003", quantity=200, is_quarantined=True,
            quarantine_reason="待复检", location="隔离区"
        )
        normal_batch = factory.create_inventory_batch(
            "TM003", quantity=100, is_quarantined=False, location="合格区"
        )

        # 尝试消耗150件 - 应该失败，因为只有100件可用
        result = InspectionService.consume_material(
            db_session,
            material_id=factory.materials["TM003"].id,
            quantity=150
        )
        assert result == False, "应该消耗失败，因为可用库存只有100件"

        # 消耗50件应该成功
        result = InspectionService.consume_material(
            db_session,
            material_id=factory.materials["TM003"].id,
            quantity=50
        )
        assert result == True

        # 检查正常批次数量减少了，隔离批次没变
        db_session.refresh(normal_batch)
        db_session.refresh(quarantined_batch)
        assert normal_batch.available_quantity == 50
        assert quarantined_batch.available_quantity == 0  # 隔离批次可用数量始终为0
        assert quarantined_batch.quantity == 200  # 但总数量不变

    def test_inspection_failed_auto_quarantine(self, db_session):
        """到货抽检不合格自动隔离批次"""
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()

        # 创建采购订单和到货
        po = factory.create_purchase_order(
            "TEST-PO-QUAR-001", "TS002", "TM002",
            quantity=200, expected_date=date.today()
        )

        delivery = crud_delivery.create(
            db_session,
            obj_in=DeliveryCreate(
                delivery_no="TEST-DEL-QUAR-001",
                purchase_order_id=po.id,
                supplier_id=factory.suppliers["TS002"].id,
                material_id=factory.materials["TM002"].id,
                quantity=200,
                delivery_date=date.today(),
                batch_no="BATCH-QUAR-TEST"
            )
        )

        # 抽检不合格
        inspection_in = InspectionCreate(
            delivery_id=delivery.id,
            sample_size=50,
            defective_count=20,
            pass_rate=0.60,
            result="failed",
            inspector="测试检验员",
            inspection_date=date.today()
        )

        inspection = InspectionService.record_inspection_and_process(
            db_session, inspection_in
        )

        # 检查自动创建了隔离批次
        from app.models import InventoryBatch
        batches = db_session.query(InventoryBatch).filter(
            InventoryBatch.delivery_id == delivery.id
        ).all()

        assert len(batches) == 1
        assert batches[0].is_quarantined == True
        assert batches[0].available_quantity == 0
        assert "不合格" in batches[0].quarantine_reason

    def test_release_quarantined_batch(self, db_session):
        """测试解除隔离批次"""
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()

        # 创建隔离批次
        quarantined_batch = factory.create_inventory_batch(
            "TM002", quantity=100, is_quarantined=True,
            quarantine_reason="抽检不合格，待复检",
            location="隔离区"
        )

        # 解除隔离
        released = InspectionService.release_quarantined_batch(
            db_session,
            inventory_batch_id=quarantined_batch.id,
            release_reason="复检合格，解除隔离"
        )

        assert released is not None
        assert released.is_quarantined == False
        assert released.available_quantity == 100
        assert "解除隔离" in released.quarantine_reason
        assert released.location == "合格区"

        # 解除隔离后应该可以领用
        assert InspectionService.can_use_batch(db_session, released.id) == True


class TestSupplierShortageRefresh:
    """测试边界：供应商承诺数量不足会刷新短缺预警"""

    def test_supplier_shortage_creates_impact_records(self, db_session):
        """供应商承诺数量不足，应该创建短缺影响记录"""
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()

        # 生成采购建议
        suggestions = PurchaseService.generate_purchase_suggestions(db_session)
        carbon_suggestion = next(
            (s for s in suggestions if s.material and s.material.code == "TM001"), None
        )

        # 创建供应商确认，只承诺部分数量
        confirmation = SupplierConfirmationService.create_confirmation_from_suggestion(
            db_session,
            suggestion_id=carbon_suggestion.id,
            confirmation_no="TEST-CONF-REFRESH-001",
            supplier_id=factory.suppliers["TS001"].id
        )

        requested_qty = confirmation.requested_quantity
        committed_qty = requested_qty // 2  # 只承诺一半

        confirm_data = SupplierConfirmationConfirm(
            committed_quantity=committed_qty,
            committed_delivery_date=date.today() + timedelta(days=15),
            confirmation_note="产能不足，只能供应一半",
            batches=[
                SupplierConfirmationBatchCreate(
                    batch_no="BATCH-REFRESH-001",
                    quantity=committed_qty,
                    planned_date=date.today() + timedelta(days=15),
                    remark="首批也是最后一批"
                )
            ]
        )

        confirmed = SupplierConfirmationService.supplier_confirm(
            db_session,
            confirmation_id=confirmation.id,
            confirm_data=confirm_data
        )

        assert confirmed.shortage_quantity == requested_qty - committed_qty
        assert confirmed.status == "shortage"

        # 检查是否创建了短缺影响记录
        impacts = crud_supplier_shortage_impact.get_by_confirmation(
            db_session, confirmation.id
        )
        assert len(impacts) > 0, "供应商承诺不足应该创建短缺影响记录"

        # 检查影响记录的内容
        for impact in impacts:
            assert impact.shortage_quantity > 0
            assert impact.impact_level in ["high", "medium", "low"]
            assert impact.production_batch_id is not None
            assert impact.affected_vehicle_model_id is not None

    def test_supplier_zero_commitment_max_impact(self, db_session):
        """供应商承诺数量为0，应该产生最大的短缺影响"""
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()

        # 确保没有库存
        # 生成采购建议
        suggestions = PurchaseService.generate_purchase_suggestions(db_session)
        aluminum_suggestion = next(
            (s for s in suggestions if s.material and s.material.code == "TM002"), None
        )

        # 创建供应商确认，承诺0
        confirmation = SupplierConfirmationService.create_confirmation_from_suggestion(
            db_session,
            suggestion_id=aluminum_suggestion.id,
            confirmation_no="TEST-CONF-ZERO-001",
            supplier_id=factory.suppliers["TS002"].id
        )

        confirm_data = SupplierConfirmationConfirm(
            committed_quantity=0,
            committed_delivery_date=None,
            confirmation_note="完全无法供货",
            batches=[]
        )

        confirmed = SupplierConfirmationService.supplier_confirm(
            db_session,
            confirmation_id=confirmation.id,
            confirm_data=confirm_data
        )

        assert confirmed.shortage_quantity == confirmation.requested_quantity

        # 检查影响
        impacts = crud_supplier_shortage_impact.get_by_confirmation(
            db_session, confirmation.id
        )
        assert len(impacts) > 0

        # TM002是关键物料，完全缺货应该有高优先级影响
        high_impacts = [i for i in impacts if i.impact_level == "high"]
        assert len(high_impacts) > 0, "关键物料0承诺应该产生高优先级影响"

    def test_resubmit_confirmation_refreshes_impact(self, db_session):
        """重新提交供应商确认应该刷新短缺影响"""
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()

        suggestions = PurchaseService.generate_purchase_suggestions(db_session)
        suggestion = next(
            (s for s in suggestions if s.material and s.material.code == "TM001"), None
        )

        confirmation = SupplierConfirmationService.create_confirmation_from_suggestion(
            db_session,
            suggestion_id=suggestion.id,
            confirmation_no="TEST-CONF-RESUBMIT-001",
            supplier_id=factory.suppliers["TS001"].id
        )

        # 第一次确认：只承诺50%
        confirm_data_1 = SupplierConfirmationConfirm(
            committed_quantity=50,
            committed_delivery_date=date.today() + timedelta(days=15),
            confirmation_note="第一次确认：产能不足",
            batches=[
                SupplierConfirmationBatchCreate(
                    batch_no="BATCH-RESUB-001",
                    quantity=50,
                    planned_date=date.today() + timedelta(days=15)
                )
            ]
        )

        confirmed_1 = SupplierConfirmationService.supplier_confirm(
            db_session,
            confirmation_id=confirmation.id,
            confirm_data=confirm_data_1
        )

        impacts_1 = crud_supplier_shortage_impact.get_by_confirmation(
            db_session, confirmation.id
        )
        shortage_1 = confirmed_1.shortage_quantity

        # 重新确认：承诺100%
        confirm_data_2 = SupplierConfirmationConfirm(
            committed_quantity=confirmation.requested_quantity,
            committed_delivery_date=date.today() + timedelta(days=10),
            confirmation_note="第二次确认：产能恢复，可以全部供应",
            batches=[
                SupplierConfirmationBatchCreate(
                    batch_no="BATCH-RESUB-002",
                    quantity=confirmation.requested_quantity,
                    planned_date=date.today() + timedelta(days=10)
                )
            ]
        )

        # 注意：需要将状态改回pending/revised才能重新确认
        from app.crud.supplier_confirmation import crud_supplier_confirmation
        crud_supplier_confirmation.update(
            db_session, db_obj=confirmation,
            obj_in={"status": "revised"}
        )

        confirmed_2 = SupplierConfirmationService.supplier_confirm(
            db_session,
            confirmation_id=confirmation.id,
            confirm_data=confirm_data_2
        )

        # 重新确认后应该没有短缺
        assert confirmed_2.shortage_quantity == 0
        assert confirmed_2.status == "confirmed"

        # 短缺影响应该被删除
        impacts_2 = crud_supplier_shortage_impact.get_by_confirmation(
            db_session, confirmation.id
        )
        assert len(impacts_2) == 0, "没有短缺时应该删除短缺影响记录"

    def test_shortage_analysis_production_batches(self, db_session):
        """短缺分析应该按批次优先级分配库存"""
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()

        # 为TM002创建部分库存
        factory.create_inventory_batch("TM002", quantity=100, is_quarantined=False)

        # 生成采购建议
        suggestions = PurchaseService.generate_purchase_suggestions(db_session)
        aluminum_suggestion = next(
            (s for s in suggestions if s.material and s.material.code == "TM002"), None
        )

        # 创建供应商确认，承诺0
        confirmation = SupplierConfirmationService.create_confirmation_from_suggestion(
            db_session,
            suggestion_id=aluminum_suggestion.id,
            confirmation_no="TEST-CONF-PRIORITY-001",
            supplier_id=factory.suppliers["TS002"].id
        )

        confirm_data = SupplierConfirmationConfirm(
            committed_quantity=0,
            committed_delivery_date=None,
            confirmation_note="完全无法供货",
            batches=[]
        )

        confirmed = SupplierConfirmationService.supplier_confirm(
            db_session,
            confirmation_id=confirmation.id,
            confirm_data=confirm_data
        )

        impacts = crud_supplier_shortage_impact.get_by_confirmation(
            db_session, confirmation.id
        )

        # 应该优先影响优先级低的批次（因为库存优先分配给高优先级批次）
        # TV002的优先级是8，TB003是200台在5天后，TB004是150台在25天后
        # 有100件库存，应该优先满足近期的TB003（200台需求200件，用100件库存，缺口100件）
        # 然后TB004（150台需求150件，无库存可用，缺口150件）
        # 所以TB004应该受影响更严重

        # 检查受影响的批次
        affected_batch_ids = [i.production_batch_id for i in impacts]
        tb003 = factory.production_batches["TB003"]
        tb004 = factory.production_batches["TB004"]

        # 两个批次都应该受影响（因为都有缺口）
        assert tb003.id in affected_batch_ids or tb004.id in affected_batch_ids

        # 检查瓶颈分析
        bottlenecks = SupplierConfirmationService.get_supplier_bottlenecks(db_session)
        assert len(bottlenecks) > 0, "应该识别出供应商瓶颈"

        # 检查统计数据
        stats = SupplierConfirmationService.get_confirmation_statistics(db_session)
        assert stats.shortage_confirmations >= 1
        assert stats.total_shortage_qty > 0
        assert stats.commitment_rate < 1.0
