import pytest
from datetime import date, timedelta
from tests.test_data_factory import DataFactory

from app.services.requirement import RequirementService
from app.services.purchase import PurchaseService
from app.services.supplier_confirmation import SupplierConfirmationService
from app.services.inspection import InspectionService
from app.services.delay_analysis import DelayAnalysisService
from app.crud.purchase import crud_inventory_batch, crud_purchase_suggestion
from app.crud.supplier_confirmation import crud_supplier_shortage_impact
from app.schemas import (
    SupplierConfirmationConfirm, SupplierConfirmationBatchCreate,
    InspectionCreate
)


class TestMainSupplyChainFlow:
    """测试完整的供应协同主链路"""

    def test_full_supply_chain_flow(self, db_session):
        """
        端到端测试主链路：
        1. 车型计划拆料 → 计算物料需求
        2. 生成采购建议
        3. 供应商确认
        4. 到货抽检
        5. 隔离不合格批次
        6. 延期反推生产批次
        """
        factory = DataFactory(db_session)
        factory.setup_basic_supply_chain()

        # === Step 1: 车型计划拆料 - 计算物料需求 ===
        requirements = RequirementService.calculate_material_requirements(db_session)

        assert len(requirements) >= 4, "应该至少有4种物料的需求"

        carbon_frame_req = next(
            (r for r in requirements if r.material_code == "TM001"), None
        )
        assert carbon_frame_req is not None, "应该有碳纤维车架的需求"
        assert carbon_frame_req.required_quantity == 80, f"TV001生产50+30=80台，每台1件，应该需要80件，实际{carbon_frame_req.required_quantity}"
        assert carbon_frame_req.is_critical == True, "碳纤维车架是关键物料"
        assert carbon_frame_req.stock_quantity == 0, "初始库存应该为0"

        # === Step 2: 生成采购建议 ===
        suggestions = PurchaseService.generate_purchase_suggestions(db_session)

        assert len(suggestions) >= 3, "应该生成至少3条采购建议"

        carbon_suggestion = next(
            (s for s in suggestions if s.material and s.material.code == "TM001"), None
        )
        aluminum_suggestion = next(
            (s for s in suggestions if s.material and s.material.code == "TM002"), None
        )
        assert carbon_suggestion is not None, "应该有碳纤维车架的采购建议"
        assert aluminum_suggestion is not None, "应该有铝合金车架的采购建议"
        expected_qty = 80 + 50  # 生产需求80 + 安全库存50
        assert carbon_suggestion.suggested_quantity >= expected_qty, \
            f"采购建议数量应该覆盖生产需求+安全库存，期望>={expected_qty}，实际{carbon_suggestion.suggested_quantity}"
        assert carbon_suggestion.status == "pending"
        assert carbon_suggestion.suggested_supplier_id is not None

        # === Step 3: 到货抽检（使用铝合金车架的采购建议先转单） ===
        # 先转单，避免供应商确认改变状态后无法转单
        po_aluminum = PurchaseService.convert_suggestion_to_order(
            db_session,
            suggestion_id=aluminum_suggestion.id,
            order_no="TEST-PO-001",
            quantity=200,
            expected_date=date.today() + timedelta(days=10)
        )

        assert po_aluminum is not None
        assert po_aluminum.order_no == "TEST-PO-001"
        assert po_aluminum.quantity == 200

        # 将PO添加到factory的字典中
        factory.purchase_orders["TEST-PO-001"] = po_aluminum

        delivery = factory.create_delivery(
            "TEST-DEL-001", "TEST-PO-001", "TS002", "TM002",
            quantity=200,
            delivery_date=date.today(),
            batch_no="TEST-BATCH-DEL-001"
        )

        assert delivery is not None

        # === Step 4: 隔离不合格批次（抽检不合格） ===
        inspection_in = InspectionCreate(
            delivery_id=delivery.id,
            sample_size=50,
            defective_count=15,
            pass_rate=0.70,
            result="failed",
            inspector="测试检验员",
            inspection_date=date.today(),
            remark="抽检50件，不合格15件，合格率70%"
        )

        inspection = InspectionService.record_inspection_and_process(
            db_session, inspection_in
        )

        assert inspection is not None
        assert inspection.result == "failed"

        # 检查库存批次是否被隔离
        from app.models import InventoryBatch
        all_batches = db_session.query(InventoryBatch).filter(
            InventoryBatch.material_id == factory.materials["TM002"].id
        ).all()
        quarantined_batch = next((b for b in all_batches if b.is_quarantined), None)
        assert quarantined_batch is not None, "抽检不合格应该自动隔离批次"
        assert quarantined_batch.available_quantity == 0, "隔离批次可用数量应该为0"
        assert quarantined_batch.quarantine_reason is not None
        assert "不合格" in quarantined_batch.quarantine_reason

        # === Step 5: 供应商确认（使用碳纤维车架的采购建议） ===
        suggestion_id = carbon_suggestion.id
        confirmation = SupplierConfirmationService.create_confirmation_from_suggestion(
            db_session,
            suggestion_id=suggestion_id,
            confirmation_no="TEST-CONF-001",
            supplier_id=factory.suppliers["TS001"].id
        )

        assert confirmation is not None
        assert confirmation.confirmation_no == "TEST-CONF-001"
        assert confirmation.requested_quantity == carbon_suggestion.suggested_quantity

        # 供应商确认部分交付并延期
        committed_qty = 100  # 部分交付，有缺口
        committed_date = date.today() + timedelta(days=25)  # 延期10天
        confirm_data = SupplierConfirmationConfirm(
            committed_quantity=committed_qty,
            committed_delivery_date=committed_date,
            confirmation_note="产能紧张，只能交付100件，延期10天",
            batches=[
                SupplierConfirmationBatchCreate(
                    batch_no="BATCH-TEST-001",
                    quantity=60,
                    planned_date=date.today() + timedelta(days=20),
                    remark="首批60件"
                ),
                SupplierConfirmationBatchCreate(
                    batch_no="BATCH-TEST-002",
                    quantity=40,
                    planned_date=date.today() + timedelta(days=25),
                    remark="二批40件"
                )
            ]
        )

        confirmed = SupplierConfirmationService.supplier_confirm(
            db_session,
            confirmation_id=confirmation.id,
            confirm_data=confirm_data
        )

        assert confirmed.committed_quantity == 100
        assert confirmed.shortage_quantity == confirmation.requested_quantity - 100
        assert confirmed.status == "shortage", "有缺口应该标记为shortage状态"

        # 检查是否生成了短缺影响
        shortage_impacts = crud_supplier_shortage_impact.get_by_confirmation(
            db_session, confirmation.id
        )
        assert len(shortage_impacts) > 0, "应该生成短缺影响记录"

        # === Step 6: 延期反推生产批次 ===
        delay_result = DelayAnalysisService.analyze_delay_impact(
            db_session,
            purchase_order_id=po_aluminum.id,
            delay_days=10
        )

        assert delay_result is not None
        assert delay_result.estimated_delay_days == 10

        if delay_result.impact_level != "none":
            assert len(delay_result.affected_batches) > 0, "应该有受影响的生产批次"
            assert delay_result.total_affected_quantity > 0

            affected_batch_nos = [b.batch_no for b in delay_result.affected_batches]
            assert "TB003" in affected_batch_nos or "TB004" in affected_batch_nos, \
                "TV002的生产批次应该受铝合金车架延期影响"

        print("主链路测试完成！")
