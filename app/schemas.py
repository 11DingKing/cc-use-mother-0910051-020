from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import date, datetime

class MaterialBase(BaseModel):
    code: str
    name: str
    category: str
    spec: Optional[str] = None
    unit: str
    safety_stock: int = 0
    is_critical: bool = False

class MaterialCreate(MaterialBase):
    pass

class MaterialUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    spec: Optional[str] = None
    unit: Optional[str] = None
    safety_stock: Optional[int] = None
    is_critical: Optional[bool] = None

class Material(MaterialBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class VehicleModelBase(BaseModel):
    code: str
    name: str
    priority: int = 5
    description: Optional[str] = None
    status: str = "active"

class VehicleModelCreate(VehicleModelBase):
    pass

class VehicleModelUpdate(BaseModel):
    name: Optional[str] = None
    priority: Optional[int] = None
    description: Optional[str] = None
    status: Optional[str] = None

class VehicleModel(VehicleModelBase):
    id: int
    created_at: datetime
    class Config:
        from_attributes = True

class BOMItemBase(BaseModel):
    vehicle_model_id: int
    material_id: int
    quantity: int
    remark: Optional[str] = None

class BOMItemCreate(BOMItemBase):
    pass

class BOMItem(BOMItemBase):
    id: int
    created_at: datetime
    material: Optional[Material] = None
    class Config:
        from_attributes = True

class VehicleModelWithBOM(VehicleModel):
    bom_items: List[BOMItem] = []
    class Config:
        from_attributes = True

class SupplierBase(BaseModel):
    code: str
    name: str
    contact: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    rating: float = 0

class SupplierCreate(SupplierBase):
    pass

class SupplierUpdate(BaseModel):
    name: Optional[str] = None
    contact: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    rating: Optional[float] = None

class Supplier(SupplierBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class SupplyCapacityBase(BaseModel):
    supplier_id: int
    material_id: int
    daily_capacity: int
    delivery_days: int
    pass_rate: float
    current_stock: int = 0
    unit_price: float = 0
    is_preferred: bool = False

class SupplyCapacityCreate(SupplyCapacityBase):
    pass

class SupplyCapacityUpdate(BaseModel):
    daily_capacity: Optional[int] = None
    delivery_days: Optional[int] = None
    pass_rate: Optional[float] = None
    current_stock: Optional[int] = None
    unit_price: Optional[float] = None
    is_preferred: Optional[bool] = None

class SupplyCapacity(SupplyCapacityBase):
    id: int
    updated_at: datetime
    supplier: Optional[Supplier] = None
    material: Optional[Material] = None
    class Config:
        from_attributes = True

class PurchaseSuggestionBase(BaseModel):
    material_id: int
    suggested_quantity: int
    reason: Optional[str] = None
    priority: int = 5
    suggested_supplier_id: Optional[int] = None
    expected_delivery_date: Optional[date] = None
    status: str = "pending"

class PurchaseSuggestionCreate(PurchaseSuggestionBase):
    pass

class PurchaseSuggestion(PurchaseSuggestionBase):
    id: int
    created_at: datetime
    material: Optional[Material] = None
    class Config:
        from_attributes = True

class PurchaseOrderBase(BaseModel):
    order_no: str
    supplier_id: int
    material_id: int
    quantity: int
    expected_date: date
    status: str = "ordered"
    remark: Optional[str] = None

class PurchaseOrderCreate(PurchaseOrderBase):
    pass

class PurchaseOrderUpdate(BaseModel):
    actual_date: Optional[date] = None
    status: Optional[str] = None
    remark: Optional[str] = None

class PurchaseOrder(PurchaseOrderBase):
    id: int
    actual_date: Optional[date] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    supplier: Optional[Supplier] = None
    material: Optional[Material] = None
    class Config:
        from_attributes = True

class DeliveryBase(BaseModel):
    delivery_no: str
    purchase_order_id: int
    supplier_id: int
    material_id: int
    quantity: int
    delivery_date: date
    batch_no: Optional[str] = None

class DeliveryCreate(DeliveryBase):
    pass

class Delivery(DeliveryBase):
    id: int
    created_at: datetime
    class Config:
        from_attributes = True

class InspectionBase(BaseModel):
    delivery_id: int
    sample_size: int
    defective_count: int = 0
    pass_rate: float
    result: str
    inspector: Optional[str] = None
    inspection_date: date
    remark: Optional[str] = None

class InspectionCreate(InspectionBase):
    pass

class Inspection(InspectionBase):
    id: int
    created_at: datetime
    class Config:
        from_attributes = True

class InventoryBatchBase(BaseModel):
    delivery_id: int
    material_id: int
    quantity: int
    available_quantity: int
    is_quarantined: bool = False
    quarantine_reason: Optional[str] = None
    location: Optional[str] = None
    expire_date: Optional[date] = None

class InventoryBatchCreate(InventoryBatchBase):
    pass

class InventoryBatchUpdate(BaseModel):
    available_quantity: Optional[int] = None
    is_quarantined: Optional[bool] = None
    quarantine_reason: Optional[str] = None
    location: Optional[str] = None

class InventoryBatch(InventoryBatchBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class AlternativeMaterialBase(BaseModel):
    material_id: int
    alternative_material_id: int
    priority: int = 1
    is_active: bool = True
    remark: Optional[str] = None

class AlternativeMaterialCreate(AlternativeMaterialBase):
    pass

class AlternativeMaterial(AlternativeMaterialBase):
    id: int
    created_at: datetime
    material: Optional[Material] = None
    alternative_material: Optional[Material] = None
    class Config:
        from_attributes = True

class AlternativeMaterialRestrictionBase(BaseModel):
    alternative_id: int
    vehicle_model_id: int
    is_allowed: bool = False
    remark: Optional[str] = None

class AlternativeMaterialRestrictionCreate(AlternativeMaterialRestrictionBase):
    pass

class AlternativeMaterialRestriction(AlternativeMaterialRestrictionBase):
    id: int
    created_at: datetime
    class Config:
        from_attributes = True

class ProductionBatchBase(BaseModel):
    batch_no: str
    vehicle_model_id: int
    quantity: int
    plan_date: date
    status: str = "planned"
    remark: Optional[str] = None

class ProductionBatchCreate(ProductionBatchBase):
    pass

class ProductionBatchUpdate(BaseModel):
    status: Optional[str] = None
    remark: Optional[str] = None

class ProductionBatch(ProductionBatchBase):
    id: int
    created_at: datetime
    vehicle_model: Optional[VehicleModel] = None
    class Config:
        from_attributes = True

class DelayImpactBase(BaseModel):
    purchase_order_id: int
    production_batch_id: int
    impact_level: str
    estimated_delay_days: int = 0
    remark: Optional[str] = None

class DelayImpactCreate(DelayImpactBase):
    pass

class DelayImpact(DelayImpactBase):
    id: int
    created_at: datetime
    production_batch: Optional[ProductionBatch] = None
    class Config:
        from_attributes = True

class MaterialRequirement(BaseModel):
    material_id: int
    material_code: str
    material_name: str
    category: str
    required_quantity: int
    stock_quantity: int
    safety_stock: int
    pending_suggestion_quantity: int = 0
    in_transit_quantity: int = 0
    gross_shortage: int = 0
    shortage: int
    priority: int
    is_critical: bool

class PurchaseSuggestionGenerateRequest(BaseModel):
    vehicle_model_priorities: Optional[List[int]] = None
    include_safety_stock: bool = True

class DelayImpactAnalysisRequest(BaseModel):
    purchase_order_id: int
    new_expected_date: Optional[date] = None
    delay_days: Optional[int] = None

class DelayImpactAnalysisResult(BaseModel):
    purchase_order_id: int
    material_name: str
    affected_batches: List[ProductionBatch]
    total_affected_quantity: int
    impact_level: str
    estimated_delay_days: int
    remark: str
    analysis_details: Optional[List[dict]] = None
    material_balance: Optional[dict] = None

class OnTimeDeliveryRate(BaseModel):
    supplier_id: int
    supplier_name: str
    total_deliveries: int
    on_time_deliveries: int
    on_time_rate: float

class MaterialShortageAlert(BaseModel):
    material_id: int
    material_code: str
    material_name: str
    category: str
    current_stock: int
    safety_stock: int
    pending_suggestion_quantity: int = 0
    in_transit_quantity: int = 0
    gross_shortage: int = 0
    shortage: int
    shortage_rate: float
    affected_vehicle_models: List[str]
    priority: int

class InspectionDefectRanking(BaseModel):
    material_id: int
    material_code: str
    material_name: str
    total_inspections: int
    failed_inspections: int
    failure_rate: float
    total_defective_count: int

class StatisticsResponse(BaseModel):
    on_time_delivery_rates: List[OnTimeDeliveryRate]
    material_shortage_alerts: List[MaterialShortageAlert]
    inspection_defect_rankings: List[InspectionDefectRanking]
    generated_at: datetime

class AlternativeCheckRequest(BaseModel):
    material_id: int
    vehicle_model_id: int
    required_quantity: int

class AlternativeCheckResult(BaseModel):
    original_material: Material
    available_alternatives: List[AlternativeMaterial]
    can_be_replaced: bool
    recommended_alternative: Optional[AlternativeMaterial] = None
    total_available_quantity: int

class SupplierConfirmationBatchBase(BaseModel):
    batch_no: str
    quantity: int
    planned_date: date
    remark: Optional[str] = None

class SupplierConfirmationBatchCreate(SupplierConfirmationBatchBase):
    pass

class SupplierConfirmationBatch(SupplierConfirmationBatchBase):
    id: int
    confirmation_id: int
    created_at: datetime
    class Config:
        from_attributes = True

class SupplierShortageImpactBase(BaseModel):
    production_batch_id: int
    affected_vehicle_model_id: int
    shortage_material_id: int
    shortage_quantity: int
    impact_level: str
    estimated_delay_days: int = 0
    remark: Optional[str] = None

class SupplierShortageImpactCreate(SupplierShortageImpactBase):
    pass

class SupplierShortageImpact(SupplierShortageImpactBase):
    id: int
    confirmation_id: int
    created_at: datetime
    production_batch: Optional[ProductionBatch] = None
    vehicle_model: Optional[VehicleModel] = None
    material: Optional[Material] = None
    class Config:
        from_attributes = True

class SupplierConfirmationBase(BaseModel):
    confirmation_no: str
    purchase_suggestion_id: int
    supplier_id: int
    material_id: int
    requested_quantity: int
    committed_quantity: int
    committed_delivery_date: Optional[date] = None
    shortage_quantity: int = 0
    status: str = "pending"
    confirmation_note: Optional[str] = None

class SupplierConfirmationCreate(SupplierConfirmationBase):
    batches: List[SupplierConfirmationBatchCreate] = []

class SupplierConfirmationConfirm(BaseModel):
    committed_quantity: int
    committed_delivery_date: Optional[date] = None
    confirmation_note: Optional[str] = None
    batches: List[SupplierConfirmationBatchCreate] = []

class SupplierConfirmationUpdate(BaseModel):
    committed_quantity: Optional[int] = None
    committed_delivery_date: Optional[date] = None
    shortage_quantity: Optional[int] = None
    status: Optional[str] = None
    confirmation_note: Optional[str] = None

class SupplierConfirmation(SupplierConfirmationBase):
    id: int
    confirmed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    supplier: Optional[Supplier] = None
    material: Optional[Material] = None
    batches: List[SupplierConfirmationBatch] = []
    shortage_impacts: List[SupplierShortageImpact] = []
    class Config:
        from_attributes = True

class SupplierConfirmationWithDetail(SupplierConfirmation):
    purchase_suggestion: Optional[PurchaseSuggestion] = None

class SupplierRecalculateShortageRequest(BaseModel):
    confirmation_id: int

class SupplierBottleneckBatch(BaseModel):
    production_batch_id: int
    production_batch_no: str
    vehicle_model_id: int
    vehicle_model_name: str
    plan_date: date
    quantity: int
    affected_materials: List[Material]
    total_shortage_quantity: int
    impact_level: str

class SupplierBottleneckAnalysis(BaseModel):
    supplier_id: int
    supplier_code: str
    supplier_name: str
    bottleneck_confirmations: List[SupplierConfirmation]
    affected_batches: List[SupplierBottleneckBatch]
    total_affected_batches: int
    total_shortage_qty: int
    average_delay_days: float
    overall_impact_level: str

class SupplierConfirmationStatistics(BaseModel):
    total_confirmations: int
    pending_confirmations: int
    confirmed_confirmations: int
    shortage_confirmations: int
    total_requested_qty: int
    total_committed_qty: int
    total_shortage_qty: int
    commitment_rate: float

class ExtendedStatisticsResponse(StatisticsResponse):
    supplier_confirmation_stats: SupplierConfirmationStatistics
    supplier_bottlenecks: List[SupplierBottleneckAnalysis]
