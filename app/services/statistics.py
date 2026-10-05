from sqlalchemy.orm import Session
from typing import List, Dict
from datetime import date, datetime, timedelta
from app.crud.supplier import crud_supplier
from app.crud.purchase import crud_delivery, crud_inspection, crud_inventory_batch
from app.crud.material import crud_material
from app.crud.vehicle import crud_vehicle
from app.schemas import (
    StatisticsResponse, OnTimeDeliveryRate,
    MaterialShortageAlert, InspectionDefectRanking,
    ExtendedStatisticsResponse
)
from app.services.supplier_confirmation import SupplierConfirmationService

class StatisticsService:
    @staticmethod
    def get_statistics(db: Session) -> StatisticsResponse:
        on_time_rates = StatisticsService._calculate_on_time_delivery_rates(db)
        shortage_alerts = StatisticsService._get_material_shortage_alerts(db)
        defect_rankings = StatisticsService._get_inspection_defect_rankings(db)
        return StatisticsResponse(
            on_time_delivery_rates=on_time_rates,
            material_shortage_alerts=shortage_alerts,
            inspection_defect_rankings=defect_rankings,
            generated_at=datetime.now()
        )

    @staticmethod
    def _calculate_on_time_delivery_rates(db: Session) -> List[OnTimeDeliveryRate]:
        suppliers = crud_supplier.get_multi(db)
        result = []
        ninety_days_ago = date.today() - timedelta(days=90)
        for supplier in suppliers:
            deliveries = crud_delivery.get_by_supplier(db, supplier.id)
            recent_deliveries = [
                d for d in deliveries
                if d.delivery_date >= ninety_days_ago
            ]
            total = len(recent_deliveries)
            if total == 0:
                continue
            on_time_count = 0
            for delivery in recent_deliveries:
                if delivery.purchase_order and delivery.purchase_order.expected_date:
                    if delivery.delivery_date <= delivery.purchase_order.expected_date:
                        on_time_count += 1
            rate = on_time_count / total if total > 0 else 0
            result.append(OnTimeDeliveryRate(
                supplier_id=supplier.id,
                supplier_name=supplier.name,
                total_deliveries=total,
                on_time_deliveries=on_time_count,
                on_time_rate=round(rate, 4)
            ))
        result.sort(key=lambda x: x.on_time_rate, reverse=True)
        return result

    @staticmethod
    def _get_material_shortage_alerts(db: Session) -> List[MaterialShortageAlert]:
        from app.crud.purchase import crud_purchase_suggestion, crud_purchase_order, crud_inventory_batch
        materials = crud_material.get_multi(db)
        result = []
        active_vehicles = crud_vehicle.get_active_models(db)
        for material in materials:
            current_stock = crud_inventory_batch.get_total_stock(db, material.id)
            safety_stock = material.safety_stock
            pending_qty = crud_purchase_suggestion.get_pending_quantity_by_material(db, material.id)
            in_transit_qty = crud_purchase_order.get_in_transit_quantity_by_material(db, material.id)

            gross_shortage = max(0, safety_stock - current_stock)
            net_shortage = max(0, gross_shortage - pending_qty - in_transit_qty)

            if net_shortage <= 0 and gross_shortage <= 0:
                continue

            shortage_for_rate = net_shortage if net_shortage > 0 else gross_shortage
            shortage_rate = shortage_for_rate / safety_stock if safety_stock > 0 else 1
            affected_models = []
            for vehicle in active_vehicles:
                bom_items = crud_vehicle.get_bom_items(db, vehicle.id)
                if any(bi.material_id == material.id for bi in bom_items):
                    affected_models.append(vehicle.name)
            result.append(MaterialShortageAlert(
                material_id=material.id,
                material_code=material.code,
                material_name=material.name,
                category=material.category,
                current_stock=current_stock,
                safety_stock=safety_stock,
                pending_suggestion_quantity=pending_qty,
                in_transit_quantity=in_transit_qty,
                gross_shortage=gross_shortage,
                shortage=net_shortage,
                shortage_rate=round(shortage_rate, 4),
                affected_vehicle_models=affected_models,
                priority=10 if material.is_critical else 5
            ))
        result.sort(key=lambda x: (-x.priority, -x.shortage_rate, -x.shortage))
        return result

    @staticmethod
    def _get_inspection_defect_rankings(db: Session) -> List[InspectionDefectRanking]:
        thirty_days_ago = date.today() - timedelta(days=30)
        all_inspections = crud_inspection.get_multi(db)
        recent_inspections = [
            insp for insp in all_inspections
            if insp.inspection_date >= thirty_days_ago
        ]
        material_stats: Dict[int, Dict] = {}
        for insp in recent_inspections:
            delivery = insp.delivery
            if not delivery:
                continue
            material_id = delivery.material_id
            if material_id not in material_stats:
                material = crud_material.get(db, material_id)
                material_stats[material_id] = {
                    "material_id": material_id,
                    "material_code": material.code if material else "",
                    "material_name": material.name if material else "",
                    "total_inspections": 0,
                    "failed_inspections": 0,
                    "total_defective_count": 0
                }
            stats = material_stats[material_id]
            stats["total_inspections"] += 1
            if insp.result != "passed":
                stats["failed_inspections"] += 1
            stats["total_defective_count"] += insp.defective_count
        result = []
        for mat_id, stats in material_stats.items():
            if stats["total_inspections"] == 0:
                continue
            failure_rate = stats["failed_inspections"] / stats["total_inspections"]
            result.append(InspectionDefectRanking(
                material_id=stats["material_id"],
                material_code=stats["material_code"],
                material_name=stats["material_name"],
                total_inspections=stats["total_inspections"],
                failed_inspections=stats["failed_inspections"],
                failure_rate=round(failure_rate, 4),
                total_defective_count=stats["total_defective_count"]
            ))
        result.sort(key=lambda x: x.failure_rate, reverse=True)
        return result

    @staticmethod
    def get_extended_statistics(db: Session) -> ExtendedStatisticsResponse:
        base = StatisticsService.get_statistics(db)
        conf_stats = SupplierConfirmationService.get_confirmation_statistics(db)
        bottlenecks = SupplierConfirmationService.get_supplier_bottlenecks(db)
        return ExtendedStatisticsResponse(
            on_time_delivery_rates=base.on_time_delivery_rates,
            material_shortage_alerts=base.material_shortage_alerts,
            inspection_defect_rankings=base.inspection_defect_rankings,
            generated_at=base.generated_at,
            supplier_confirmation_stats=conf_stats,
            supplier_bottlenecks=bottlenecks
        )
