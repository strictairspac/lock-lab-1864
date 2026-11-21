import os,logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy import text
from src.config.database import Base,engine
from src.models import entities
from src.controllers.auth import router as auth
from src.routes import samples,transfers,trials,audit,shifts
from src.middlewares.errors import ErrorMiddleware
from src.middlewares.request_id import RequestIdMiddleware
@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    if os.getenv('SEED_DEMO','true')=='true':
        try:
            from scripts.seed_demo import seed
            seed()
        except Exception: logging.exception('seed failed; manual retry is available')
    try:
        from src.services.shifts import ensure_shift_global
        ensure_shift_global()
    except Exception: logging.exception('shift bootstrap failed')
    yield
app=FastAPI(title='实验样本流转并发台',lifespan=lifespan)
app.add_middleware(ErrorMiddleware);app.add_middleware(RequestIdMiddleware)
for r in (auth,samples.router,transfers.router,trials.router,audit.router,shifts.router): app.include_router(r)
@app.get('/health')
def health():
    with engine.connect() as c: c.execute(text('SELECT 1'))
    from src.services.trials import queue
    queue.ping()
    return {'status':'healthy'}
