"""Real PostgreSQL row-lock experiment. Events coordinate two database connections; no timing simulation.

阶段划分与交班账的关系：
`execute` 只做双连接竞争并在提交事务时落回执（含班次归属）；
`verify` 独立计算不变量并落核验结论；
`settle` 与签字换班共享班次指针行锁，决定入原账还是补记。
三步相互独立，任一步中断都可重试，不会重复入账。
"""
import threading,time,hashlib,json
from sqlalchemy import text,select
from src.config.database import engine,Session
from src.models.entities import Trial,Transfer,TrialBalance,Receipt,Sample
from src.services.shifts import attribute_receipt

def execute(trial_id):
    """运行双连接演练：A 持锁提交回执并落班次，B 超时回滚。返回事实，不下结论。"""
    with Session() as db:
        trial=db.get(Trial,trial_id);transfer=db.get(Transfer,trial.transfer_id);sample=db.get(Sample,transfer.sample_id)
        timeout=trial.timeout_ms;qty=transfer.quantity;initial=sample.quantity;room=transfer.destination
        trial.status='running';db.commit()
    acquired=threading.Event(); contender_done=threading.Event(); traces=[]; errors=[]
    def holder():
        try:
            with engine.connect() as c:
                tx=c.begin();pid=c.execute(text('SELECT pg_backend_pid()')).scalar()
                c.execute(text('SELECT remaining FROM trial_balances WHERE trial_id=:id FOR UPDATE'),{'id':trial_id}).one()
                traces.append({'actor':'A','pid':pid,'event':'row_lock_acquired'})
                # A receipt made before B starts is uncommitted, and B cannot read it.
                c.execute(text("INSERT INTO receipts(trial_id,actor,quantity) VALUES (:id,'A',:qty)"),{'id':trial_id,'qty':qty})
                acquired.set()
                if not contender_done.wait(12): raise RuntimeError('竞争事务未在保护期限内结束')
                changed=c.execute(text('UPDATE trial_balances SET remaining=remaining-:qty,received=received+:qty WHERE trial_id=:id AND remaining>=:qty'),{'id':trial_id,'qty':qty})
                if changed.rowcount!=1: raise RuntimeError('可用量校验失败')
                # 回执“落库时所在班次”：在 COMMIT 前取班次指针行锁并写入归属，锁持续到提交。
                # 签字换班在同一行上串行，因此回执要么落在签字前，要么落在换班后，不会两处都记。
                shift_id=attribute_receipt(c,trial_id,room)
                traces.append({'actor':'A','pid':pid,'event':'receipt_attributed','shift_id':shift_id})
                tx.commit();traces.append({'actor':'A','pid':pid,'event':'committed','quantity':qty})
        except Exception as e: errors.append(str(e));acquired.set();contender_done.set()
    def contender():
        try:
            if not acquired.wait(5): raise RuntimeError('持锁事务未就绪')
            with engine.connect() as c:
                tx=c.begin();pid=c.execute(text('SELECT pg_backend_pid()')).scalar()
                c.execute(text(f"SET LOCAL lock_timeout = '{int(timeout)}ms'"))
                c.execute(text("INSERT INTO receipts(trial_id,actor,quantity) VALUES (:id,'B',:qty)"),{'id':trial_id,'qty':qty})
                t=time.perf_counter()
                try:
                    c.execute(text('SELECT remaining FROM trial_balances WHERE trial_id=:id FOR UPDATE'),{'id':trial_id}).one()
                    raise RuntimeError('竞争连接意外获得锁')
                except Exception as e:
                    state=getattr(getattr(e,'orig',None),'sqlstate',None)
                    tx.rollback()
                    if state!='55P03': raise
                    traces.append({'actor':'B','pid':pid,'event':'rolled_back','sqlstate':state,'wait_ms':round((time.perf_counter()-t)*1000,2),'receipt_insert_rolled_back':True})
        except Exception as e: errors.append(str(e))
        finally: contender_done.set()
    a=threading.Thread(target=holder);b=threading.Thread(target=contender)
    a.start();b.start();a.join(15);b.join(15)
    if a.is_alive() or b.is_alive(): raise RuntimeError('并发试验未及时结束')
    if errors: raise RuntimeError(';'.join(errors))
    with Session() as db:
        balance=db.get(TrialBalance,trial_id)
        receipts=list(db.scalars(select(Receipt).where(Receipt.trial_id==trial_id)))
        trial=db.get(Trial,trial_id);trial.status='running';db.commit()
        return {'mode':'真实 PostgreSQL / 隔离样本镜像账本','sample':sample.code,'initial':initial,
                'transfer_quantity':qty,'room':room,'remaining':balance.remaining,'received':balance.received,
                'receipt_count':len(receipts),'receipt_actor':receipts[0].actor if len(receipts)==1 else None,
                'traces':traces,
                'sql':['SELECT ... FOR UPDATE','SET LOCAL lock_timeout','INSERT INTO receipts','UPDATE trial_balances','ROLLBACK / COMMIT'],
                'formula':'remaining + received = initial；A 回执 1 条，B 回执回滚为 0 条'}


def verify(trial_id, facts):
    """核验结论：只看已提交的事实。回执不唯一、守恒不成立或核验失败都不入账。"""
    invariant=(facts['remaining']+facts['received']==facts['initial'] and facts['remaining']>=0
               and facts['receipt_count']==1 and facts['receipt_actor']=='A')
    result={**facts,'invariant_passed':invariant}
    result['checksum']=hashlib.sha256(json.dumps(result,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    with Session() as db:
        trial=db.get(Trial,trial_id);trial.result=result
        trial.status='completed' if invariant else 'failed';db.commit()
    return result


def settle(trial_id):
    """核验通过后入账，独立事务、可安全重试。

    与签字换班串行：先锁班次指针；班次已签字则补记回原班次。
    入账失败不回滚演练结论，交班账页显示「已核验待补记」，补记端点可继续续记。
    """
    from src.services.shifts import account_trial
    with Session() as db:
        trial=db.get(Trial,trial_id)
        if not trial or trial.status!='completed': return None
        passed=bool((trial.result or {}).get('invariant_passed'))
    if not passed: return None
    try:
        kind=account_trial(trial_id)
    except Exception as e:
        with Session() as db:
            trial=db.get(Trial,trial_id)
            if trial:
                trial.result={**(trial.result or {}),'ledger_pending':f'入账待补记：{e}'}
                db.commit()
        return None
    with Session() as db:
        trial=db.get(Trial,trial_id)
        if trial:
            trial.result={**(trial.result or {}),'ledger_kind':kind}
            db.commit()
    return kind
