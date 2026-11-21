from fastapi import APIRouter, Depends
from src.config.database import db_session
from src.utils.security import current, writer
from src.schemas.shift import ShiftSign
from src.services import shifts as service

router = APIRouter()


@router.get('/shifts')
def listing(db=Depends(db_session), user=Depends(current)):
    return service.listing(db)


@router.get('/shifts/current')
def current_shift(db=Depends(db_session), user=Depends(current)):
    return service.current_detail(db)


@router.get('/shifts/{ident}')
def detail(ident: int, db=Depends(db_session), user=Depends(current)):
    return service.detail(db, ident)


@router.post('/shifts/sign')
def sign(data: ShiftSign, db=Depends(db_session), user=Depends(writer)):
    return service.sign(db, data.shift_id, user, data.next_code)


@router.post('/shifts/{ident}/backfill')
def backfill(ident: int, db=Depends(db_session), user=Depends(writer)):
    return service.backfill(db, ident, user)
