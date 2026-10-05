from sqlalchemy.orm import Session
from datetime import date, timedelta
from typing import Dict, Tuple

from app.crud.material import crud_material
from app.crud.vehicle import crud_vehicle, crud_production_batch
from app.crud.supplier import crud_supplier, crud_supply_capacity
from app.crud.alternative import crud_alternative_material, crud_alternative_restriction
from app.crud.purchase import (
    crud_purchase_suggestion, crud_purchase_order, crud_delivery,
    crud_inspection, crud_inventory_batch
)
from app.crud.supplier_confirmation import crud_supplier_confirmation

from app.schemas import (
    MaterialCreate, VehicleModelCreate, BOMItemCreate,
    SupplierCreate, SupplyCapacityCreate, ProductionBatchCreate,
    AlternativeMaterialCreate, AlternativeMaterialRestrictionCreate,
    PurchaseSuggestionCreate, PurchaseOrderCreate, DeliveryCreate,
    InspectionCreate, InventoryBatchCreate
)


class DataFactory:
    """测试数据工厂 - 创建干净的测试数据，不污染示例库"""

    def __init__(self, db: Session):
        self.db = db
        self.materials: Dict[str, object] = {}
        self.vehicle_models: Dict[str, object] = {}
        self.suppliers: Dict[str, object] = {}
        self.supply_capacities: Dict[str, object] = {}
        self.production_batches: Dict[str, object] = {}
        self.alternatives: Dict[str, object] = {}
        self.purchase_suggestions: Dict[str, object] = {}
        self.purchase_orders: Dict[str, object] = {}
        self.deliveries: Dict[str, object] = {}
        self.inventory_batches: Dict[str, object] = {}
        self.inspections: Dict[str, object] = {}
        self.confirmations: Dict[str, object] = {}

    def create_material(self, code: str, name: str, category: str,
                        unit: str, safety_stock: int = 0,
                        is_critical: bool = False, spec: str = None):
        if not crud_material.get_by_code(self.db, code):
            mat = crud_material.create(
                self.db,
                obj_in=MaterialCreate(
                    code=code, name=name, category=category,
                    spec=spec, unit=unit, safety_stock=safety_stock,
                    is_critical=is_critical
                )
            )
        else:
            mat = crud_material.get_by_code(self.db, code)
        self.materials[code] = mat
        return mat

    def create_vehicle_model(self, code: str, name: str, priority: int = 5,
                             status: str = "active", description: str = None):
        if not crud_vehicle.get_by_code(self.db, code):
            vm = crud_vehicle.create(
                self.db,
                obj_in=VehicleModelCreate(
                    code=code, name=name, priority=priority,
                    status=status, description=description
                )
            )
        else:
            vm = crud_vehicle.get_by_code(self.db, code)
        self.vehicle_models[code] = vm
        return vm

    def add_bom_item(self, vehicle_code: str, material_code: str, quantity: int):
        vehicle_id = self.vehicle_models[vehicle_code].id
        material_id = self.materials[material_code].id
        existing = crud_vehicle.get_bom_items(self.db, vehicle_id)
        if not any(bm.material_id == material_id for bm in existing):
            crud_vehicle.add_bom_item(
                self.db,
                vehicle_model_id=vehicle_id,
                bom_item_in=BOMItemCreate(
                    vehicle_model_id=vehicle_id,
                    material_id=material_id,
                    quantity=quantity
                )
            )

    def create_supplier(self, code: str, name: str, contact: str = None,
                        phone: str = None, address: str = None, rating: float = 4.5):
        if not crud_supplier.get_by_code(self.db, code):
            sup = crud_supplier.create(
                self.db,
                obj_in=SupplierCreate(
                    code=code, name=name, contact=contact,
                    phone=phone, address=address, rating=rating
                )
            )
        else:
            sup = crud_supplier.get_by_code(self.db, code)
        self.suppliers[code] = sup
        return sup

    def create_supply_capacity(self, supplier_code: str, material_code: str,
                               daily_capacity: int, delivery_days: int,
                               pass_rate: float, current_stock: int = 0,
                               unit_price: float = 0, is_preferred: bool = True):
        sup_id = self.suppliers[supplier_code].id
        mat_id = self.materials[material_code].id
        existing = crud_supply_capacity.get_by_supplier_and_material(self.db, sup_id, mat_id)
        if not existing:
            sc = crud_supply_capacity.create(
                self.db,
                obj_in=SupplyCapacityCreate(
                    supplier_id=sup_id, material_id=mat_id,
                    daily_capacity=daily_capacity, delivery_days=delivery_days,
                    pass_rate=pass_rate, current_stock=current_stock,
                    unit_price=unit_price, is_preferred=is_preferred
                )
            )
            self.supply_capacities[f"{supplier_code}_{material_code}"] = sc
            return sc
        self.supply_capacities[f"{supplier_code}_{material_code}"] = existing
        return existing

    def create_production_batch(self, batch_no: str, vehicle_code: str,
                                quantity: int, plan_date: date = None,
                                status: str = "planned"):
        vehicle_id = self.vehicle_models[vehicle_code].id
        if not crud_production_batch.get_by_batch_no(self.db, batch_no):
            pb = crud_production_batch.create(
                self.db,
                obj_in=ProductionBatchCreate(
                    batch_no=batch_no, vehicle_model_id=vehicle_id,
                    quantity=quantity, plan_date=plan_date or (date.today() + timedelta(days=10)),
                    status=status
                )
            )
        else:
            pb = crud_production_batch.get_by_batch_no(self.db, batch_no)
        self.production_batches[batch_no] = pb
        return pb

    def create_alternative_material(self, material_code: str, alt_material_code: str,
                                    priority: int = 1, is_active: bool = True,
                                    remark: str = None):
        mat_id = self.materials[material_code].id
        alt_mat_id = self.materials[alt_material_code].id
        existing = crud_alternative_material.get_by_materials(self.db, mat_id, alt_mat_id)
        if not existing:
            alt = crud_alternative_material.create(
                self.db,
                obj_in=AlternativeMaterialCreate(
                    material_id=mat_id, alternative_material_id=alt_mat_id,
                    priority=priority, is_active=is_active, remark=remark
                )
            )
            self.alternatives[f"{material_code}_{alt_material_code}"] = alt
            return alt
        self.alternatives[f"{material_code}_{alt_material_code}"] = existing
        return existing

    def create_alternative_restriction(self, alt_key: str, vehicle_code: str, is_allowed: bool,
                                       remark: str = None):
        alt_id = self.alternatives[alt_key].id
        vehicle_id = self.vehicle_models[vehicle_code].id
        existing = crud_alternative_restriction.get_by_alternative_and_vehicle(
            self.db, alt_id, vehicle_id
        )
        if not existing:
            res = crud_alternative_restriction.create(
                self.db,
                obj_in=AlternativeMaterialRestrictionCreate(
                    alternative_id=alt_id, vehicle_model_id=vehicle_id,
                    is_allowed=is_allowed, remark=remark
                )
            )
            return res
        return existing

    def create_inventory_batch(self, material_code: str, quantity: int,
                               available_quantity: int = None,
                               is_quarantined: bool = False,
                               quarantine_reason: str = None,
                               location: str = "合格区-A-01"):
        mat = self.materials[material_code]
        final_available = 0 if is_quarantined else (available_quantity if available_quantity is not None else quantity)
        batch = crud_inventory_batch.create(
            self.db,
            obj_in=InventoryBatchCreate(
                delivery_id=0, material_id=mat.id,
                quantity=quantity,
                available_quantity=final_available,
                is_quarantined=is_quarantined,
                quarantine_reason=quarantine_reason,
                location=location
            )
        )
        self.inventory_batches[f"{material_code}_{len(self.inventory_batches)}"] = batch
        return batch

    def create_purchase_suggestion(self, material_code: str, quantity: int,
                                   supplier_code: str = None, reason: str = None,
                                   priority: int = 5, expected_days: int = 10,
                                   status: str = "pending"):
        mat_id = self.materials[material_code].id
        sup_id = self.suppliers[supplier_code].id if supplier_code else None
        suggestion = crud_purchase_suggestion.create(
            self.db,
            obj_in=PurchaseSuggestionCreate(
                material_id=mat_id, suggested_quantity=quantity,
                reason=reason or f"测试采购需求{quantity}",
                priority=priority,
                suggested_supplier_id=sup_id,
                expected_delivery_date=date.today() + timedelta(days=expected_days),
                status=status
            )
        )
        self.purchase_suggestions[material_code] = suggestion
        return suggestion

    def create_purchase_order(self, order_no: str, supplier_code: str,
                              material_code: str, quantity: int,
                              expected_date: date = None, status: str = "ordered"):
        sup_id = self.suppliers[supplier_code].id
        mat_id = self.materials[material_code].id
        if not crud_purchase_order.get_by_order_no(self.db, order_no):
            po = crud_purchase_order.create(
                self.db,
                obj_in=PurchaseOrderCreate(
                    order_no=order_no, supplier_id=sup_id,
                    material_id=mat_id, quantity=quantity,
                    expected_date=expected_date or (date.today() + timedelta(days=7)),
                    status=status
                )
            )
        else:
            po = crud_purchase_order.get_by_order_no(self.db, order_no)
        self.purchase_orders[order_no] = po
        return po

    def create_delivery(self, delivery_no: str, po_code: str, supplier_code: str,
                        material_code: str, quantity: int,
                        delivery_date: date = None, batch_no: str = None):
        po_id = self.purchase_orders[po_code].id
        sup_id = self.suppliers[supplier_code].id
        mat_id = self.materials[material_code].id
        if not crud_delivery.get_by_delivery_no(self.db, delivery_no):
            delivery = crud_delivery.create(
                self.db,
                obj_in=DeliveryCreate(
                    delivery_no=delivery_no, purchase_order_id=po_id,
                    supplier_id=sup_id, material_id=mat_id,
                    quantity=quantity,
                    delivery_date=delivery_date or date.today(),
                    batch_no=batch_no or f"BATCH-TEST-{delivery_no}"
                )
            )
        else:
            delivery = crud_delivery.get_by_delivery_no(self.db, delivery_no)
        self.deliveries[delivery_no] = delivery
        return delivery

    def create_inspection(self, delivery_key: str, sample_size: int,
                          defective_count: int, pass_rate: float,
                          result: str, inspector: str = "测试检验员",
                          inspection_date: date = None):
        delivery = self.deliveries[delivery_key]
        if not crud_inspection.get_by_delivery(self.db, delivery.id):
            inspection = crud_inspection.create(
                self.db,
                obj_in=InspectionCreate(
                    delivery_id=delivery.id,
                    sample_size=sample_size,
                    defective_count=defective_count,
                    pass_rate=pass_rate,
                    result=result,
                    inspector=inspector,
                    inspection_date=inspection_date or date.today()
                )
            )
        else:
            inspection = crud_inspection.get_by_delivery(self.db, delivery.id)
        self.inspections[delivery_key] = inspection
        return inspection

    def setup_basic_supply_chain(self) -> Tuple[Dict, Dict, Dict]:
        """
        设置基础供应链数据:
        - 2个车型
        - 4种物料（2种关键物料）
        - 2个供应商
        - 生产批次
        - BOM清单
        """
        mat_carbon_frame = self.create_material(
            "TM001", "测试碳纤维车架", "车架", "件",
            safety_stock=50, is_critical=True, spec="测试用27.5寸全碳"
        )
        mat_aluminum_frame = self.create_material(
            "TM002", "测试铝合金车架", "车架", "件",
            safety_stock=100, is_critical=True, spec="测试用26寸6061铝"
        )
        mat_chain_11s = self.create_material(
            "TM003", "测试11速链条", "传动", "条",
            safety_stock=200, is_critical=False, spec="测试用11S防锈"
        )
        mat_chain_10s = self.create_material(
            "TM004", "测试10速链条", "传动", "条",
            safety_stock=200, is_critical=False, spec="测试用10S标准"
        )

        vm_pro = self.create_vehicle_model(
            "TV001", "测试旗舰PRO", priority=10,
            description="测试用专业竞赛级车"
        )
        vm_city = self.create_vehicle_model(
            "TV002", "测试城市CITY", priority=8,
            description="测试用城市通勤车"
        )

        self.add_bom_item("TV001", "TM001", 1)
        self.add_bom_item("TV001", "TM003", 1)
        self.add_bom_item("TV002", "TM002", 1)
        self.add_bom_item("TV002", "TM004", 1)

        sup_carbon = self.create_supplier(
            "TS001", "测试碳纤维科技", "张经理", "13800000001",
            "测试地址1", rating=4.9
        )
        sup_aluminum = self.create_supplier(
            "TS002", "测试铝合金制品", "李厂长", "13800000002",
            "测试地址2", rating=4.5
        )

        self.create_supply_capacity(
            "TS001", "TM001", daily_capacity=300, delivery_days=15,
            pass_rate=0.99, current_stock=60, unit_price=1500, is_preferred=True
        )
        self.create_supply_capacity(
            "TS002", "TM002", daily_capacity=500, delivery_days=10,
            pass_rate=0.985, current_stock=120, unit_price=350, is_preferred=True
        )
        self.create_supply_capacity(
            "TS002", "TM003", daily_capacity=1000, delivery_days=8,
            pass_rate=0.995, current_stock=250, unit_price=80, is_preferred=True
        )
        self.create_supply_capacity(
            "TS002", "TM004", daily_capacity=1200, delivery_days=6,
            pass_rate=0.992, current_stock=300, unit_price=65, is_preferred=True
        )

        self.create_production_batch(
            "TB001", "TV001", 50, date.today() + timedelta(days=10)
        )
        self.create_production_batch(
            "TB002", "TV001", 30, date.today() + timedelta(days=30)
        )
        self.create_production_batch(
            "TB003", "TV002", 200, date.today() + timedelta(days=5)
        )
        self.create_production_batch(
            "TB004", "TV002", 150, date.today() + timedelta(days=25)
        )

        return self.materials, self.vehicle_models, self.suppliers
