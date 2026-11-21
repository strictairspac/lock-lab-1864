"""交班账：班次指针、回执归属、签字换班与可恢复补记。

并发口径
--------
`shift_state` 只有一行，回执落库（A 事务提交前）与签字换班都在这一行上取行锁，
因此二者严格串行：回执要么落在签字前（原账），要么落在签字后（新班次）。
核验入账同样先锁该行，再读班次是否已签字，保证一笔接收量只会进原账或补记。
`ledger_entries.trial_id` 唯一约束是最后一道幂等闸门。
"""
from datetime import datetime, timezone
from sqlalchemy import text, select
from fastapi import HTTPException
from src.config.database import Session
from src.models.entities import Shift, ShiftState, ShiftLedger, Receipt, Audit
from src.utils.serialization import record

CURRENT_ID = 1


def _now():
    return datetime.now(timezone.utc)


def ensure_shift(db):
    """确保存在当前班次指针；并发启动（backend/worker）也只会建一个班次。"""
    if db.scalar(select(ShiftState.id).where(ShiftState.id == CURRENT_ID)):
        return db.get(ShiftState, CURRENT_ID)
    existing = db.scalar(select(Shift.id).order_by(Shift.id))
    if existing:
        shift_id = existing
    else:
        shift_id = db.execute(text(
            "INSERT INTO shifts(code) VALUES ('shift-20261117') "
            "ON CONFLICT (code) DO UPDATE SET code=EXCLUDED.code RETURNING id")).scalar()
    db.execute(text(
        'INSERT INTO shift_state(id,current_shift_id) VALUES (:i,:s) '
        'ON CONFLICT (id) DO NOTHING'), {'i': CURRENT_ID, 's': shift_id})
    db.commit()
    return db.get(ShiftState, CURRENT_ID)


def ensure_shift_global():
    with Session() as db:
        ensure_shift(db)


def lock_current(db):
    """锁住班次指针行并返回当前班次 id。调用方事务提交后释放。"""
    row = db.execute(text('SELECT current_shift_id FROM shift_state WHERE id=:i FOR UPDATE'),
                     {'i': CURRENT_ID}).first()
    if not row:
        raise HTTPException(503, '班次指针未初始化')
    return row[0]


def lock_current_conn(conn):
    return conn.execute(text('SELECT current_shift_id FROM shift_state WHERE id=:i FOR UPDATE'),
                        {'i': CURRENT_ID}).scalar()


def next_code(db, code):
    n = 2
    while db.scalar(select(Shift.id).where(Shift.code == f'{code}-{n}')):
        n += 1
    return f'{code}-{n}'


def attribute_receipt(conn, trial_id, room):
    """在回执事务内落班次归属；与签字换班共享 shift_state 行锁。"""
    shift_id = lock_current_conn(conn)
    conn.execute(text("UPDATE receipts SET shift_id=:s, room=:r WHERE trial_id=:t AND actor='A'"),
                 {'s': shift_id, 'r': room, 't': trial_id})
    return shift_id


def account_locked(db, trial_id, current_shift_id):
    """核验通过后入账。调用方必须已持有 shift_state 行锁。

    返回 'original' / 'backfill' / None（无回执、重复入账或核验未通过）。
    未带班次归属的旧回执按最早班次处理，即随该班次补记。
    """
    receipt = db.scalar(select(Receipt).where(Receipt.trial_id == trial_id, Receipt.actor == 'A'))
    if not receipt:
        return None
    shift_id = receipt.shift_id or db.scalar(select(Shift.id).order_by(Shift.id)) or current_shift_id
    shift = db.get(Shift, shift_id)
    if not shift:
        return None
    kind = 'backfill' if shift.signed_at else 'original'
    room = receipt.room or ''
    inserted = db.execute(text(
        'INSERT INTO ledger_entries(trial_id,shift_id,room,quantity,kind,created_at) '
        'VALUES (:t,:s,:r,:q,:k,:at) ON CONFLICT (trial_id) DO NOTHING RETURNING id'),
        {'t': trial_id, 's': shift_id, 'r': room, 'q': receipt.quantity, 'k': kind, 'at': _now()}).first()
    if not inserted:
        return None  # 同一笔已入账，重试不重复
    db.execute(text(
        'INSERT INTO shift_ledgers(shift_id,room,original_quantity,backfill_quantity) '
        'VALUES (:s,:r,:o,:b) ON CONFLICT (shift_id,room) DO UPDATE SET '
        'original_quantity=shift_ledgers.original_quantity+:o, '
        'backfill_quantity=shift_ledgers.backfill_quantity+:b'),
        {'s': shift_id, 'r': room, 'o': receipt.quantity if kind == 'original' else 0,
         'b': receipt.quantity if kind == 'backfill' else 0})
    return kind


def account_trial(trial_id):
    """单笔入账（独立事务）。核验与补记共用，可安全重试。"""
    with Session() as db:
        current = lock_current(db)
        kind = account_locked(db, trial_id, current)
        db.commit()
        return kind


def sign(db, shift_id, user, next_code_value=None):
    current = lock_current(db)
    if shift_id != current:
        raise HTTPException(409, '该班次已签字或不是当前班次')
    shift = db.get(Shift, current)
    if not shift or shift.signed_at:
        raise HTTPException(409, '该班次已签字')
    code = (next_code_value or '').strip() or next_code(db, shift.code)
    if db.scalar(select(Shift.id).where(Shift.code == code)):
        raise HTTPException(409, '下一班次编号已存在')
    nxt = Shift(code=code)
    db.add(nxt)
    db.flush()
    shift.signed_at = _now()
    shift.signed_by = user.email
    shift.next_shift_id = nxt.id
    db.execute(text('UPDATE shift_state SET current_shift_id=:n WHERE id=:i'),
               {'n': nxt.id, 'i': CURRENT_ID})
    db.add(Audit(actor=user.email, action=f'交班签字 {shift.code} → {nxt.code}'))
    db.commit()
    return {'shift': detail(db, shift.id), 'next': detail(db, nxt.id)}


def _ledger_rows(db, shift_id):
    return [{'room': row.room, 'original': row.original_quantity, 'backfill': row.backfill_quantity,
             'total': row.original_quantity + row.backfill_quantity}
            for row in db.scalars(select(ShiftLedger).where(ShiftLedger.shift_id == shift_id)
                                  .order_by(ShiftLedger.room))]


def _entry_rows(db, shift_id):
    rows = db.execute(text(
        "SELECT le.trial_id,t.name,le.room,le.quantity,le.kind,le.created_at "
        "FROM ledger_entries le JOIN trials t ON t.id=le.trial_id "
        "WHERE le.shift_id=:s ORDER BY le.id"), {'s': shift_id})
    return [{'trial_id': r.trial_id, 'trial_name': r.name, 'room': r.room, 'quantity': r.quantity,
             'kind': r.kind, 'created_at': r.created_at.isoformat() if r.created_at else None}
            for r in rows]


def _pending_rows(db, shift_id, current):
    """回执已落库、但该班次账上还没有对应记录的演练。

    未核验的演练在签字后仍可能核验通过并补记，因此列在「待补记」；
    核验失败的不入账，不列出。
    """
    rows = db.execute(text(
        "SELECT t.id,t.name,t.status,tr.destination,r.quantity,le.id AS entry_id "
        "FROM trials t "
        "JOIN receipts r ON r.trial_id=t.id AND r.actor='A' "
        "JOIN transfers tr ON tr.id=t.transfer_id "
        "LEFT JOIN ledger_entries le ON le.trial_id=t.id "
        "WHERE COALESCE(r.shift_id,:cur)=:s AND le.id IS NULL AND t.status<>'failed' "
        "ORDER BY t.id"), {'s': shift_id, 'cur': current})
    return [{'trial_id': r.id, 'trial_name': r.name, 'status': r.status, 'room': r.destination,
             'quantity': r.quantity,
             'reason': '已核验待补记' if r.status == 'completed' else '尚未核验'} for r in rows]


def detail(db, shift_id):
    shift = db.get(Shift, shift_id)
    if not shift:
        raise HTTPException(404, '班次不存在')
    state = db.get(ShiftState, CURRENT_ID)
    current = state.current_shift_id if state else shift_id
    return {
        **record(shift),
        'is_current': shift.id == current,
        'status': 'signed' if shift.signed_at else 'open',
        'ledger': _ledger_rows(db, shift.id),
        'entries': _entry_rows(db, shift.id),
        'pending': _pending_rows(db, shift.id, current),
    }


def current_detail(db):
    ensure_shift(db)
    current = lock_current(db)
    db.commit()
    return detail(db, current)


def listing(db):
    ensure_shift(db)
    return [detail(db, s.id) for s in db.scalars(select(Shift).order_by(Shift.id))]


def backfill(db, shift_id, user):
    """可恢复补记：逐笔独立事务，中断后重跑即可续记；同一笔不会重复入账。"""
    shift = db.get(Shift, shift_id)
    if not shift:
        raise HTTPException(404, '班次不存在')
    candidates = db.execute(text(
        "SELECT t.id FROM trials t JOIN receipts r ON r.trial_id=t.id AND r.actor='A' "
        "LEFT JOIN ledger_entries le ON le.trial_id=t.id "
        "WHERE COALESCE(r.shift_id,:cur)=:s AND t.status='completed' "
        "AND (t.result->>'invariant_passed')::boolean AND le.id IS NULL "
        "ORDER BY t.id"), {'s': shift_id, 'cur': shift_id}).scalars().all()
    db.rollback()
    processed = 0
    for trial_id in candidates:
        if account_trial(trial_id):
            processed += 1
    db.add(Audit(actor=user.email, action=f'补记交班账 {shift.code}：{processed} 笔'))
    db.commit()
    return {'processed': processed, 'total': len(candidates), **detail(db, shift_id)}
