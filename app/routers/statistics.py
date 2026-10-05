from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas import StatisticsResponse, ExtendedStatisticsResponse
from app.services.statistics import StatisticsService

router = APIRouter(prefix="/statistics", tags=["统计报表"])

@router.get("/", response_model=StatisticsResponse)
def get_all_statistics(db: Session = Depends(get_db)):
    return StatisticsService.get_statistics(db)

@router.get("/extended", response_model=ExtendedStatisticsResponse)
def get_extended_statistics(db: Session = Depends(get_db)):
    return StatisticsService.get_extended_statistics(db)

@router.get("/on-time-delivery")
def get_on_time_delivery_rates(db: Session = Depends(get_db)):
    stats = StatisticsService.get_statistics(db)
    return {"on_time_delivery_rates": stats.on_time_delivery_rates}

@router.get("/material-shortage")
def get_material_shortage_alerts(db: Session = Depends(get_db)):
    stats = StatisticsService.get_statistics(db)
    return {"material_shortage_alerts": stats.material_shortage_alerts}

@router.get("/defect-ranking")
def get_inspection_defect_rankings(db: Session = Depends(get_db)):
    stats = StatisticsService.get_statistics(db)
    return {"inspection_defect_rankings": stats.inspection_defect_rankings}
