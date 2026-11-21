from sqlalchemy import Column, Integer, String, JSON, DateTime, ForeignKey, Text, UniqueConstraint
from datetime import datetime, timezone
from src.config.database import Base
class User(Base):
    __tablename__='users'
    id=Column(Integer,primary_key=True);email=Column(String(120),unique=True,nullable=False)
    password=Column(String(300),nullable=False);role=Column(String(20),nullable=False)
class Sample(Base):
    __tablename__='samples'
    id=Column(Integer,primary_key=True);code=Column(String(80),unique=True,nullable=False)
    title=Column(String(160),nullable=False);quantity=Column(Integer,nullable=False);location=Column(String(120),nullable=False)
class Transfer(Base):
    __tablename__='transfers'
    id=Column(Integer,primary_key=True);code=Column(String(80),unique=True,nullable=False)
    sample_id=Column(Integer,ForeignKey('samples.id'),nullable=False);destination=Column(String(120),nullable=False)
    quantity=Column(Integer,nullable=False);note=Column(String(300),default='')
class Trial(Base):
    __tablename__='trials'
    id=Column(Integer,primary_key=True);name=Column(String(120),nullable=False)
    transfer_id=Column(Integer,ForeignKey('transfers.id'),nullable=False);timeout_ms=Column(Integer,nullable=False)
    status=Column(String(20),default='queued',nullable=False);result=Column(JSON,nullable=True)
    created_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc));error=Column(Text,default='')
class TrialBalance(Base):
    __tablename__='trial_balances'
    trial_id=Column(Integer,ForeignKey('trials.id'),primary_key=True);remaining=Column(Integer,nullable=False);received=Column(Integer,nullable=False,default=0)
class Receipt(Base):
    __tablename__='receipts'
    id=Column(Integer,primary_key=True);trial_id=Column(Integer,ForeignKey('trials.id'),nullable=False)
    actor=Column(String(20),nullable=False);quantity=Column(Integer,nullable=False)
    # 落库时所在班次：A 事务提交前写入，签字换班与此行共享锁串行化。
    shift_id=Column(Integer,ForeignKey('shifts.id'),nullable=True);room=Column(String(120),default='')
    __table_args__=(UniqueConstraint('trial_id','actor'),)
class Shift(Base):
    __tablename__='shifts'
    id=Column(Integer,primary_key=True);code=Column(String(80),unique=True,nullable=False)
    opened_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
    signed_at=Column(DateTime(timezone=True),nullable=True);signed_by=Column(String(120),nullable=True)
    next_shift_id=Column(Integer,ForeignKey('shifts.id'),nullable=True)
class ShiftState(Base):
    """单行指针：始终指向当前未签字的班次，回执落库与签字换班都在此串行。"""
    __tablename__='shift_state'
    id=Column(Integer,primary_key=True);current_shift_id=Column(Integer,ForeignKey('shifts.id'),nullable=False)
class ShiftLedger(Base):
    """按接收室汇总的交班账：原账在签字时定格，补记只增不减。"""
    __tablename__='shift_ledgers'
    id=Column(Integer,primary_key=True);shift_id=Column(Integer,ForeignKey('shifts.id'),nullable=False)
    room=Column(String(120),nullable=False);original_quantity=Column(Integer,nullable=False,default=0)
    backfill_quantity=Column(Integer,nullable=False,default=0)
    __table_args__=(UniqueConstraint('shift_id','room'),)
class LedgerEntry(Base):
    """一笔演练接收量只能入账一次：trial_id 唯一，原账或补记二选一。"""
    __tablename__='ledger_entries'
    id=Column(Integer,primary_key=True);trial_id=Column(Integer,ForeignKey('trials.id'),nullable=False,unique=True)
    shift_id=Column(Integer,ForeignKey('shifts.id'),nullable=False);room=Column(String(120),nullable=False)
    quantity=Column(Integer,nullable=False);kind=Column(String(20),nullable=False)
    created_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
class Audit(Base):
    __tablename__='audit_logs'
    id=Column(Integer,primary_key=True);actor=Column(String(120),nullable=False);action=Column(String(160),nullable=False)
    created_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
