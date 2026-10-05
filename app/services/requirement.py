from sqlalchemy.orm import Session
from typing import List, Dict, Optional
from datetime import date, timedelta
from app.crud.vehicle import crud_vehicle, crud_production_batch
from app.crud.material import crud_material
from app.crud.purchase import crud_inventory_batch, crud_purchase_suggestion, crud_purchase_order
from app.schemas import MaterialRequirement

class RequirementService:
    @staticmethod
    def calculate_material_requirements(
        db: Session,
        vehicle_model_priorities: Optional[List[int]] = None,
        include_safety_stock: bool = True
    ) -> List[MaterialRequirement]:
        active_models = crud_vehicle.get_active_models(db)
        if vehicle_model_priorities:
            active_models = [
                m for m in active_models if m.priority in vehicle_model_priorities
            ]
        active_models.sort(key=lambda m: m.priority, reverse=True)
        material_requirements: Dict[int, Dict] = {}
        for vehicle_model in active_models:
            bom_items = crud_vehicle.get_bom_items(db, vehicle_model.id)
            planned_batches = crud_production_batch.get_by_vehicle_model(db, vehicle_model.id)
            planned_batches = [b for b in planned_batches if b.status == "planned"]
            total_production_quantity = sum(b.quantity for b in planned_batches)
            for bom_item in bom_items:
                material_id = bom_item.material_id
                required_qty = bom_item.quantity * total_production_quantity
                if material_id not in material_requirements:
                    material = crud_material.get(db, material_id)
                    stock_qty = crud_inventory_batch.get_total_stock(db, material_id)
                    pending_qty = crud_purchase_suggestion.get_pending_quantity_by_material(db, material_id)
                    in_transit_qty = crud_purchase_order.get_in_transit_quantity_by_material(db, material_id)
                    material_requirements[material_id] = {
                        "material_id": material_id,
                        "material_code": material.code if material else "",
                        "material_name": material.name if material else "",
                        "category": material.category if material else "",
                        "required_quantity": 0,
                        "stock_quantity": stock_qty,
                        "safety_stock": material.safety_stock if material else 0,
                        "pending_suggestion_quantity": pending_qty,
                        "in_transit_quantity": in_transit_qty,
                        "priority": vehicle_model.priority,
                        "is_critical": material.is_critical if material else False,
                    }
                material_requirements[material_id]["required_quantity"] += required_qty
                if vehicle_model.priority > material_requirements[material_id]["priority"]:
                    material_requirements[material_id]["priority"] = vehicle_model.priority
        result = []
        for mat_id, req in material_requirements.items():
            total_needed = req["required_quantity"]
            if include_safety_stock:
                total_needed += req["safety_stock"]
            gross_shortage = max(0, total_needed - req["stock_quantity"])
            net_shortage = max(0, gross_shortage - req["pending_suggestion_quantity"] - req["in_transit_quantity"])
            result.append(MaterialRequirement(
                material_id=req["material_id"],
                material_code=req["material_code"],
                material_name=req["material_name"],
                category=req["category"],
                required_quantity=req["required_quantity"],
                stock_quantity=req["stock_quantity"],
                safety_stock=req["safety_stock"],
                pending_suggestion_quantity=req["pending_suggestion_quantity"],
                in_transit_quantity=req["in_transit_quantity"],
                gross_shortage=gross_shortage,
                shortage=net_shortage,
                priority=req["priority"],
                is_critical=req["is_critical"],
            ))
        result.sort(key=lambda x: (-x.is_critical, -x.priority, -x.shortage))
        return result

    @staticmethod
    def get_requirements_by_vehicle_model(
        db: Session,
        vehicle_model_id: int
    ) -> List[MaterialRequirement]:
        vehicle_model = crud_vehicle.get(db, vehicle_model_id)
        if not vehicle_model:
            return []
        bom_items = crud_vehicle.get_bom_items(db, vehicle_model_id)
        planned_batches = crud_production_batch.get_by_vehicle_model(db, vehicle_model_id)
        planned_batches = [b for b in planned_batches if b.status == "planned"]
        total_production_quantity = sum(b.quantity for b in planned_batches)
        result = []
        for bom_item in bom_items:
            material = crud_material.get(db, bom_item.material_id)
            if not material:
                continue
            stock_qty = crud_inventory_batch.get_total_stock(db, bom_item.material_id)
            required_qty = bom_item.quantity * total_production_quantity
            pending_qty = crud_purchase_suggestion.get_pending_quantity_by_material(db, bom_item.material_id)
            in_transit_qty = crud_purchase_order.get_in_transit_quantity_by_material(db, bom_item.material_id)
            gross_shortage = max(0, required_qty + material.safety_stock - stock_qty)
            net_shortage = max(0, gross_shortage - pending_qty - in_transit_qty)
            result.append(MaterialRequirement(
                material_id=material.id,
                material_code=material.code,
                material_name=material.name,
                category=material.category,
                required_quantity=required_qty,
                stock_quantity=stock_qty,
                safety_stock=material.safety_stock,
                pending_suggestion_quantity=pending_qty,
                in_transit_quantity=in_transit_qty,
                gross_shortage=gross_shortage,
                shortage=net_shortage,
                priority=vehicle_model.priority,
                is_critical=material.is_critical,
            ))
        return result
