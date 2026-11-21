import {table} from '../components/common/table.js';
import {badge} from '../components/common/badge.js';
import {esc,date} from '../utils/html.js';

const kind={original:'原账',backfill:'补记'};

function shiftPanel(shift,canWrite){
  if(!shift) return '';
  const signed=shift.status==='signed';
  const ledgerRows=shift.ledger.length?shift.ledger.map(r=>[esc(r.room),`${r.original} 份`,`${r.backfill} 份`,`<strong>${r.total} 份</strong>`]):[];
  const entryRows=shift.entries.map(e=>[`#${e.trial_id} ${esc(e.trial_name)}`,esc(e.room),`${e.quantity} 份`,`<span class="badge ${e.kind}">${kind[e.kind]||esc(e.kind)}</span>`,date(e.created_at)]);
  const pendingRows=shift.pending.map(p=>[`#${p.trial_id} ${esc(p.trial_name)}`,esc(p.room),`${p.quantity} 份`,badge(p.status),esc(p.reason)]);
  return `<div class="section-top"><h2>交班账 · ${esc(shift.code)}</h2><span>${signed?`已签字 ${date(shift.signed_at)} · ${esc(shift.signed_by)}`:'未签字 · 当前班次'}</span></div>
    <div class="shift-actions">
      ${signed?'<span class="notice inline">该班次已签字，原账留原数；此后核验通过的量以「补记」记回本班次。</span>':'<span class="notice inline">签字即换班。未出结论的演练也可签，之后核验通过的按原班次补记。</span>'}
      ${canWrite&&!signed&&shift.is_current?`<div class="sign-row"><input id="next-code" placeholder="下一班次 ${esc(shift.code)}-2" aria-label="下一班次编号"><button id="sign-shift" data-id="${shift.id}">签字并换班 →</button></div>`:''}
      ${canWrite&&signed&&shift.pending.length?`<button id="backfill" data-id="${shift.id}">补记待入账 ${shift.pending.filter(p=>p.status==='completed').length} 笔 ↻</button>`:''}
    </div>
    <h3 class="sub">按接收室核对</h3>
    ${ledgerRows.length?table(['接收室','原账（签字定格）','补记（核验后）','合计'],ledgerRows):'<div class="empty">本班次还没有入库的接收量。回执落库后需演练核验通过才入账。</div>'}
    <h3 class="sub">入账明细</h3>
    ${entryRows.length?table(['演练','接收室','接收量','入账口径','时间'],entryRows):'<div class="empty">暂无入账记录。</div>'}
    ${pendingRows.length?`<h3 class="sub">待补记 / 未核验</h3>${table(['演练','接收室','接收量','状态','说明'],pendingRows)}`:''}`;
}

export function evidencePage(trial,shift,canWrite){
  if(!trial)return `${shift?shiftPanel(shift,canWrite):''}<div class="empty">尚无冲突证据。请到「并发执行」发起演练，完成后在此复核事务回滚结果。</div>`;
  const r=trial.result;
  const receipt=trial.receipt?`<p class="receipt-line">回执接收室：<strong>${esc(trial.receipt.room)}</strong> · 接收量 <strong>${trial.receipt.quantity} 份</strong> · ${trial.receipt.shift_id?`落库班次 #${trial.receipt.shift_id}`:'未归属班次'}</p>`:'';
  return `<div class="evidence-top"><div><span class="eyebrow">EVIDENCE / #${trial.id}</span><h2>${esc(trial.name)} ${badge(trial.status)}</h2><p>${date(trial.created_at)} · PostgreSQL 真实双连接</p></div><button id="export" data-id="${trial.id}">导出 JSON ↓</button></div>${trial.error?`<div class="error">${esc(trial.error)}。请检查配置后重新发起演练。</div>`:''}${r?`<div class="verdict"><div class="verdict-icon">${r.invariant_passed?'✓':'!'}</div><div><span class="eyebrow">CONSERVATION CHECK</span><h2>${r.invariant_passed?'回滚完整，数量守恒':'不变量检查未通过'}</h2><p>${r.initial} 份 = 剩余 ${r.remaining} 份 + 已接收 ${r.received} 份 · 提交回执 ${r.receipt_count} 条</p>${receipt}</div><div class="equation">${r.remaining}<span> + </span>${r.received}<span> = </span>${r.initial}</div></div><div class="section-top"><h2>数据库事务轨迹</h2><span>证据来自实际连接与错误码</span></div>${table(['连接','Backend PID','事务事件','等待 / SQLSTATE'],r.traces.map(t=>[`<strong>事务 ${esc(t.actor)}</strong>`,t.pid,esc(t.event),t.sqlstate?`${t.wait_ms} ms / ${esc(t.sqlstate)}`:(t.event==='row_lock_acquired'?'已获取行锁':t.event==='receipt_attributed'?`班次 #${t.shift_id}`:'提交成功')]))}<div class="evidence-notes"><section><h3>判定口径</h3><p>${esc(r.formula)}</p><p>B 事务先插入的回执，已随 lock_timeout 事务回滚。A 持锁等待由线程事件协调，未伪造等待结果。</p></section><section><h3>复现摘要</h3><p>${esc(r.mode)}</p><code>${esc(r.checksum)}</code></section></div>`:'<div class="notice">任务正在后台执行，页面每 1.5 秒刷新。可以保留此页或稍后从运行列表返回。</div>'}${shiftPanel(shift,canWrite)}`;
}
