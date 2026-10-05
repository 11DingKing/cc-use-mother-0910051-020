from sqlalchemy.orm import Session
from datetime import date, timedelta
from app.database import Base, engine
from app.crud.material import crud_material
from app.crud.vehicle import crud_vehicle, crud_production_batch
from app.crud.supplier import crud_supplier, crud_supply_capacity
from app.crud.alternative import crud_alternative_material, crud_alternative_restriction
from app.crud.purchase import (
    crud_purchase_order, crud_delivery, crud_inspection,
    crud_inventory_batch, crud_purchase_suggestion
)
from app.crud.supplier_confirmation import (
    crud_supplier_confirmation, crud_supplier_confirmation_batch
)
from app.schemas import (
    MaterialCreate, VehicleModelCreate, BOMItemCreate,
    SupplierCreate, SupplyCapacityCreate, ProductionBatchCreate,
    AlternativeMaterialCreate, AlternativeMaterialRestrictionCreate,
    PurchaseOrderCreate, DeliveryCreate, InspectionCreate,
    InventoryBatchCreate, PurchaseSuggestionCreate,
    SupplierConfirmationCreate, SupplierConfirmationBatchCreate
)

def init_db():
    Base.metadata.create_all(bind=engine)

def seed_all(db: Session):
    materials = seed_materials(db)
    vehicle_models = seed_vehicle_models(db)
    seed_bom_items(db, vehicle_models, materials)
    suppliers = seed_suppliers(db)
    seed_supply_capacities(db, suppliers, materials)
    production_batches = seed_production_batches(db, vehicle_models)
    seed_alternative_materials(db, materials, vehicle_models)
    seed_purchase_data(db, suppliers, materials)
    suggestions = seed_purchase_suggestions(db, materials, suppliers)
    seed_supplier_confirmations(db, suggestions, materials, suppliers, vehicle_models, production_batches)
    print("种子数据初始化完成！")

def seed_materials(db: Session):
    print("正在创建物料数据...")
    materials_data = [
        {"code": "M001", "name": "碳纤维车架", "category": "车架", "spec": "27.5寸 全碳", "unit": "件", "safety_stock": 50, "is_critical": True},
        {"code": "M002", "name": "铝合金车架", "category": "车架", "spec": "26寸 6061铝", "unit": "件", "safety_stock": 100, "is_critical": True},
        {"code": "M003", "name": "11速链条", "category": "传动", "spec": "11S 防锈", "unit": "条", "safety_stock": 200, "is_critical": False},
        {"code": "M004", "name": "10速链条", "category": "传动", "spec": "10S 标准", "unit": "条", "safety_stock": 200, "is_critical": False},
        {"code": "M005", "name": "G5级钢珠", "category": "钢珠", "spec": "6.35mm G5", "unit": "颗", "safety_stock": 5000, "is_critical": True},
        {"code": "M006", "name": "G10级钢珠", "category": "钢珠", "spec": "6.35mm G10", "unit": "颗", "safety_stock": 8000, "is_critical": False},
        {"code": "M007", "name": "G16级钢珠", "category": "钢珠", "spec": "6.35mm G16", "unit": "颗", "safety_stock": 10000, "is_critical": False},
        {"code": "M008", "name": "碳纤维辐条轮", "category": "轮组", "spec": "27.5寸 32孔", "unit": "组", "safety_stock": 80, "is_critical": True},
        {"code": "M009", "name": "铝合金辐条轮", "category": "轮组", "spec": "26寸 32孔", "unit": "组", "safety_stock": 120, "is_critical": False},
        {"code": "M010", "name": "11速卡式飞轮", "category": "传动", "spec": "11-32T", "unit": "个", "safety_stock": 150, "is_critical": True},
        {"code": "M011", "name": "10速卡式飞轮", "category": "传动", "spec": "11-34T", "unit": "个", "safety_stock": 180, "is_critical": False},
        {"code": "M012", "name": "11速变速套件", "category": "变速", "spec": "指拨+后拨", "unit": "套", "safety_stock": 100, "is_critical": True},
        {"code": "M013", "name": "10速变速套件", "category": "变速", "spec": "指拨+后拨", "unit": "套", "safety_stock": 120, "is_critical": False},
        {"code": "M014", "name": "碟刹套装", "category": "制动", "spec": "前后碟刹", "unit": "套", "safety_stock": 150, "is_critical": False},
        {"code": "M015", "name": "V刹套装", "category": "制动", "spec": "前后V刹", "unit": "套", "safety_stock": 200, "is_critical": False},
    ]
    materials = {}
    for data in materials_data:
        if not crud_material.get_by_code(db, data["code"]):
            mat = crud_material.create(db, obj_in=MaterialCreate(**data))
            materials[data["code"]] = mat
        else:
            materials[data["code"]] = crud_material.get_by_code(db, data["code"])
    return materials

def seed_vehicle_models(db: Session):
    print("正在创建车型计划...")
    vehicles_data = [
        {"code": "V001", "name": "竞速旗舰X1", "priority": 10, "description": "专业竞赛级碳纤维全避震山地车", "status": "active"},
        {"code": "V002", "name": "城市通勤C1", "priority": 8, "description": "铝合金城市通勤车", "status": "active"},
        {"code": "V003", "name": "山地越野M1", "priority": 9, "description": "中高端碳纤维山地车", "status": "active"},
        {"code": "V004", "name": "入门休闲R1", "priority": 5, "description": "铝合金入门休闲车", "status": "active"},
    ]
    vehicles = {}
    for data in vehicles_data:
        if not crud_vehicle.get_by_code(db, data["code"]):
            vm = crud_vehicle.create(db, obj_in=VehicleModelCreate(**data))
            vehicles[data["code"]] = vm
        else:
            vehicles[data["code"]] = crud_vehicle.get_by_code(db, data["code"])
    return vehicles

def seed_bom_items(db: Session, vehicles, materials):
    print("正在创建BOM清单...")
    bom_data = [
        ("V001", [("M001", 1), ("M003", 1), ("M005", 40), ("M008", 1), ("M010", 1), ("M012", 1), ("M014", 1)]),
        ("V002", [("M002", 1), ("M004", 1), ("M007", 36), ("M009", 1), ("M011", 1), ("M013", 1), ("M015", 1)]),
        ("V003", [("M001", 1), ("M003", 1), ("M006", 40), ("M008", 1), ("M010", 1), ("M012", 1), ("M014", 1)]),
        ("V004", [("M002", 1), ("M004", 1), ("M007", 36), ("M009", 1), ("M011", 1), ("M013", 1), ("M015", 1)]),
    ]
    for vehicle_code, items in bom_data:
        vehicle_id = vehicles[vehicle_code].id
        existing_bom = crud_vehicle.get_bom_items(db, vehicle_id)
        if not existing_bom:
            for mat_code, qty in items:
                mat_id = materials[mat_code].id
                crud_vehicle.add_bom_item(
                    db,
                    vehicle_model_id=vehicle_id,
                    bom_item_in=BOMItemCreate(
                        vehicle_model_id=vehicle_id,
                        material_id=mat_id,
                        quantity=qty
                    )
                )

def seed_suppliers(db: Session):
    print("正在创建供应商数据...")
    suppliers_data = [
        {"code": "S001", "name": "华通精密钢珠有限公司", "contact": "张经理", "phone": "13800138001", "address": "浙江省宁波市镇海区", "rating": 4.8},
        {"code": "S002", "name": "天鸿碳纤维科技", "contact": "李总", "phone": "13900139002", "address": "江苏省无锡市新区", "rating": 4.9},
        {"code": "S003", "name": "恒达传动系统", "contact": "王工", "phone": "13700137003", "address": "广东省东莞市", "rating": 4.6},
        {"code": "S004", "name": "顺达铝合金制品", "contact": "赵厂长", "phone": "13600136004", "address": "山东省聊城市", "rating": 4.5},
        {"code": "S005", "name": "盛泰轮组制造", "contact": "孙经理", "phone": "13500135005", "address": "天津市武清区", "rating": 4.7},
    ]
    suppliers = {}
    for data in suppliers_data:
        if not crud_supplier.get_by_code(db, data["code"]):
            sup = crud_supplier.create(db, obj_in=SupplierCreate(**data))
            suppliers[data["code"]] = sup
        else:
            suppliers[data["code"]] = crud_supplier.get_by_code(db, data["code"])
    return suppliers

def seed_supply_capacities(db: Session, suppliers, materials):
    print("正在创建供应能力数据...")
    capacity_data = [
        ("S001", "M005", 50000, 7, 0.998, 8000, 0.15, True),
        ("S001", "M006", 80000, 5, 0.995, 12000, 0.08, True),
        ("S001", "M007", 100000, 3, 0.990, 15000, 0.05, False),
        ("S002", "M001", 300, 15, 0.990, 60, 1500, True),
        ("S004", "M002", 500, 10, 0.985, 120, 350, True),
        ("S003", "M003", 1000, 8, 0.995, 250, 80, True),
        ("S003", "M004", 1200, 6, 0.992, 300, 65, False),
        ("S003", "M010", 600, 12, 0.988, 180, 180, True),
        ("S003", "M011", 700, 10, 0.985, 220, 150, False),
        ("S003", "M012", 500, 15, 0.990, 120, 280, True),
        ("S003", "M013", 600, 12, 0.988, 150, 220, False),
        ("S005", "M008", 400, 14, 0.992, 90, 420, True),
        ("S005", "M009", 600, 10, 0.988, 140, 280, True),
        ("S003", "M014", 800, 8, 0.990, 180, 120, True),
        ("S003", "M015", 1000, 6, 0.985, 240, 80, False),
    ]
    for sup_code, mat_code, daily_cap, del_days, pass_rate, stock, price, preferred in capacity_data:
        sup_id = suppliers[sup_code].id
        mat_id = materials[mat_code].id
        existing = crud_supply_capacity.get_by_supplier_and_material(db, sup_id, mat_id)
        if not existing:
            crud_supply_capacity.create(
                db,
                obj_in=SupplyCapacityCreate(
                    supplier_id=sup_id,
                    material_id=mat_id,
                    daily_capacity=daily_cap,
                    delivery_days=del_days,
                    pass_rate=pass_rate,
                    current_stock=stock,
                    unit_price=price,
                    is_preferred=preferred
                )
            )

def seed_production_batches(db: Session, vehicles):
    print("正在创建生产批次数据...")
    today = date.today()
    batch_data = [
        ("B001", "V001", 50, today + timedelta(days=10), "planned"),
        ("B002", "V001", 30, today + timedelta(days=30), "planned"),
        ("B003", "V002", 200, today + timedelta(days=5), "planned"),
        ("B004", "V002", 150, today + timedelta(days=25), "planned"),
        ("B005", "V003", 80, today + timedelta(days=8), "planned"),
        ("B006", "V003", 60, today + timedelta(days=28), "planned"),
        ("B007", "V004", 300, today + timedelta(days=3), "planned"),
        ("B008", "V004", 250, today + timedelta(days=20), "planned"),
    ]
    batches = {}
    for batch_code, vehicle_code, qty, plan_date, status in batch_data:
        if not crud_production_batch.get_by_batch_no(db, batch_code):
            vm_id = vehicles[vehicle_code].id
            batch = crud_production_batch.create(
                db,
                obj_in=ProductionBatchCreate(
                    batch_no=batch_code,
                    vehicle_model_id=vm_id,
                    quantity=qty,
                    plan_date=plan_date,
                    status=status
                )
            )
            batches[batch_code] = batch
    return batches

def seed_alternative_materials(db: Session, materials, vehicles):
    print("正在创建替代料规则...")
    alt_data = [
        ("M005", "M006", 1, True, "G10级可在紧急情况下替代G5级"),
        ("M005", "M007", 2, True, "G16级仅可用于最低端车型"),
        ("M006", "M007", 1, True, "G16级可替代G10级用于非关键部位"),
        ("M003", "M004", 1, False, "10速链条可应急替代11速"),
        ("M008", "M009", 1, False, "铝轮可应急替代碳轮"),
    ]
    for mat_code, alt_code, priority, active, remark in alt_data:
        mat_id = materials[mat_code].id
        alt_mat_id = materials[alt_code].id
        existing = crud_alternative_material.get_by_materials(db, mat_id, alt_mat_id)
        if not existing:
            alt = crud_alternative_material.create(
                db,
                obj_in=AlternativeMaterialCreate(
                    material_id=mat_id,
                    alternative_material_id=alt_mat_id,
                    priority=priority,
                    is_active=active,
                    remark=remark
                )
            )
            if mat_code == "M005" and alt_code == "M007":
                alt_id = alt.id
                crud_alternative_restriction.create(
                    db,
                    obj_in=AlternativeMaterialRestrictionCreate(
                        alternative_id=alt_id,
                        vehicle_model_id=vehicles["V001"].id,
                        is_allowed=False,
                        remark="竞速旗舰X1不允许使用G16级钢珠"
                    )
                )
                crud_alternative_restriction.create(
                    db,
                    obj_in=AlternativeMaterialRestrictionCreate(
                        alternative_id=alt_id,
                        vehicle_model_id=vehicles["V003"].id,
                        is_allowed=False,
                        remark="山地越野M1不允许使用G16级钢珠"
                    )
                )
                crud_alternative_restriction.create(
                    db,
                    obj_in=AlternativeMaterialRestrictionCreate(
                        alternative_id=alt_id,
                        vehicle_model_id=vehicles["V004"].id,
                        is_allowed=True,
                        remark="入门休闲R1允许使用G16级钢珠"
                    )
                )

def seed_purchase_data(db: Session, suppliers, materials):
    print("正在创建采购数据...")
    today = date.today()
    
    po_data = [
        ("PO001", "S002", "M001", 100, today + timedelta(days=15), "ordered"),
        ("PO002", "S001", "M005", 50000, today + timedelta(days=7), "ordered"),
        ("PO003", "S001", "M006", 80000, today - timedelta(days=2), "delayed"),
        ("PO004", "S003", "M003", 2000, today + timedelta(days=8), "ordered"),
        ("PO005", "S005", "M008", 150, today - timedelta(days=5), "partial"),
    ]
    purchase_orders = {}
    for po_no, sup_code, mat_code, qty, exp_date, status in po_data:
        if not crud_purchase_order.get_by_order_no(db, po_no):
            sup_id = suppliers[sup_code].id
            mat_id = materials[mat_code].id
            po = crud_purchase_order.create(
                db,
                obj_in=PurchaseOrderCreate(
                    order_no=po_no,
                    supplier_id=sup_id,
                    material_id=mat_id,
                    quantity=qty,
                    expected_date=exp_date,
                    status=status,
                    remark=f"{materials[mat_code].name}采购订单"
                )
            )
            purchase_orders[po_no] = po
        else:
            purchase_orders[po_no] = crud_purchase_order.get_by_order_no(db, po_no)
    
    delivery_data = [
        ("D001", "PO005", "S005", "M008", 50, today - timedelta(days=4), "BAT20260601"),
        ("D002", "PO003", "S001", "M006", 40000, today - timedelta(days=1), "BAT20260602"),
    ]
    for del_no, po_no, sup_code, mat_code, qty, del_date, batch_no in delivery_data:
        if not crud_delivery.get_by_delivery_no(db, del_no):
            po_id = purchase_orders[po_no].id
            sup_id = suppliers[sup_code].id
            mat_id = materials[mat_code].id
            delivery = crud_delivery.create(
                db,
                obj_in=DeliveryCreate(
                    delivery_no=del_no,
                    purchase_order_id=po_id,
                    supplier_id=sup_id,
                    material_id=mat_id,
                    quantity=qty,
                    delivery_date=del_date,
                    batch_no=batch_no
                )
            )
            insp_data = [
                (delivery.id, 50, 0, 1.0, "passed", "李检验", today - timedelta(days=3)),
            ]
            if del_no == "D002":
                insp_data = [
                    (delivery.id, 200, 15, 0.925, "failed", "王检验", today),
                ]
            for del_id, sample_size, defective, pass_rate, result, inspector, insp_date in insp_data:
                if not crud_inspection.get_by_delivery(db, del_id):
                    from app.services.inspection import InspectionService
                    InspectionService.record_inspection_and_process(
                        db,
                        InspectionCreate(
                            delivery_id=del_id,
                            sample_size=sample_size,
                            defective_count=defective,
                            pass_rate=pass_rate,
                            result=result,
                            inspector=inspector,
                            inspection_date=insp_date,
                            remark=f"抽检{sample_size}件，不合格{defective}件"
                        )
                    )
    
    good_delivery = crud_delivery.get_by_delivery_no(db, "D001")
    if good_delivery:
        batches = crud_inventory_batch.get_by_material(db, good_delivery.material_id)
        if not batches:
            crud_inventory_batch.create(
                db,
                obj_in=InventoryBatchCreate(
                    delivery_id=good_delivery.id,
                    material_id=good_delivery.material_id,
                    quantity=good_delivery.quantity,
                    available_quantity=good_delivery.quantity,
                    is_quarantined=False,
                    location="合格区-A-01"
                )
            )
    
    initial_stock_data = [
        (materials["M006"].id, "D001", 12000, "合格区-B-02"),
        (materials["M007"].id, "D001", 15000, "合格区-B-03"),
        (materials["M004"].id, "D001", 300, "合格区-C-01"),
        (materials["M009"].id, "D001", 140, "合格区-C-02"),
    ]
    for mat_id, del_no_ref, qty, location in initial_stock_data:
        existing = crud_inventory_batch.get_by_material(db, mat_id)
        if not existing:
            first_delivery = crud_delivery.get_multi(db, limit=1)[0]
            crud_inventory_batch.create(
                db,
                obj_in=InventoryBatchCreate(
                    delivery_id=first_delivery.id,
                    material_id=mat_id,
                    quantity=qty,
                    available_quantity=qty,
                    is_quarantined=False,
                    location=location
                )
            )

def seed_purchase_suggestions(db: Session, materials, suppliers):
    print("正在创建采购建议数据...")
    today = date.today()
    suggestions_data = [
        ("M001", 200, "生产需求150+安全库存补充50", 10, "S002", today + timedelta(days=15), "pending"),
        ("M005", 80000, "生产需求60000+安全库存补充20000", 10, "S001", today + timedelta(days=7), "pending"),
        ("M008", 200, "关键物料，生产缺口", 9, "S005", today + timedelta(days=14), "pending"),
        ("M010", 400, "生产需求280+安全库存补充120", 8, "S003", today + timedelta(days=12), "pending"),
        ("M003", 1500, "生产需求1300+安全库存补充200", 7, "S003", today + timedelta(days=8), "pending"),
    ]
    suggestions = {}
    for mat_code, qty, reason, prio, sup_code, exp_date, status in suggestions_data:
        existing_list = crud_purchase_suggestion.get_by_material(db, materials[mat_code].id)
        existing_pending = [s for s in existing_list if s.status in ("pending", "sent_to_supplier")]
        if not existing_pending:
            sugg = crud_purchase_suggestion.create(
                db,
                obj_in=PurchaseSuggestionCreate(
                    material_id=materials[mat_code].id,
                    suggested_quantity=qty,
                    reason=reason,
                    priority=prio,
                    suggested_supplier_id=suppliers[sup_code].id,
                    expected_delivery_date=exp_date,
                    status=status
                )
            )
            suggestions[mat_code] = sugg
        else:
            suggestions[mat_code] = existing_pending[0]
    return suggestions

def seed_supplier_confirmations(db: Session, suggestions, materials, suppliers, vehicle_models, production_batches):
    print("正在创建供应商协同确认数据...")
    today = date.today()
    from app.services.supplier_confirmation import SupplierConfirmationService
    from app.schemas import SupplierConfirmationConfirm

    confirmations_data = [
        {
            "mat_code": "M001",
            "conf_no": "CONF-2026-001",
            "sup_code": "S002",
            "committed_qty": 150,
            "committed_date": today + timedelta(days=20),
            "note": "产能紧张，最多供150件，分两批交付",
            "batches": [
                ("B-S002-01", 80, today + timedelta(days=15), "首批80件"),
                ("B-S002-02", 70, today + timedelta(days=20), "二批70件"),
            ],
            "confirm": True
        },
        {
            "mat_code": "M005",
            "conf_no": "CONF-2026-002",
            "sup_code": "S001",
            "committed_qty": 80000,
            "committed_date": today + timedelta(days=7),
            "note": "产能充足，可按需求供应",
            "batches": [
                ("B-S001-01", 50000, today + timedelta(days=5), "首批5万颗"),
                ("B-S001-02", 30000, today + timedelta(days=7), "二批3万颗"),
            ],
            "confirm": True
        },
        {
            "mat_code": "M008",
            "conf_no": "CONF-2026-003",
            "sup_code": "S005",
            "committed_qty": 100,
            "committed_date": today + timedelta(days=25),
            "note": "模具检修，交货延期11天，仅能提供100件",
            "batches": [
                ("B-S005-01", 60, today + timedelta(days=20), "首批60组"),
                ("B-S005-02", 40, today + timedelta(days=25), "二批40组"),
            ],
            "confirm": True
        },
        {
            "mat_code": "M010",
            "conf_no": "CONF-2026-004",
            "sup_code": "S003",
            "committed_qty": 0,
            "committed_date": None,
            "note": None,
            "batches": [],
            "confirm": False
        },
    ]
    for data in confirmations_data:
        if data["mat_code"] not in suggestions or not suggestions[data["mat_code"]]:
            continue
        sugg = suggestions[data["mat_code"]]
        existing = crud_supplier_confirmation.get_by_confirmation_no(db, data["conf_no"])
        if existing:
            continue
        try:
            conf = SupplierConfirmationService.create_confirmation_from_suggestion(
                db,
                suggestion_id=sugg.id,
                confirmation_no=data["conf_no"],
                supplier_id=suppliers[data["sup_code"]].id
            )
            if data["confirm"]:
                batches = [
                    SupplierConfirmationBatchCreate(
                        batch_no=b[0], quantity=b[1], planned_date=b[2], remark=b[3]
                    ) for b in data["batches"]
                ]
                SupplierConfirmationService.supplier_confirm(
                    db,
                    confirmation_id=conf.id,
                    confirm_data=SupplierConfirmationConfirm(
                        committed_quantity=data["committed_qty"],
                        committed_delivery_date=data["committed_date"],
                        confirmation_note=data["note"],
                        batches=batches
                    )
                )
        except Exception as e:
            print(f"创建供应商确认 {data['conf_no']} 时出错: {e}")
            db.rollback()
            continue
