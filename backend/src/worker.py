import time,traceback,json
from pathlib import Path
from src.config.database import Session
from src.models.entities import Trial
from src.services.trials import queue
from src.services.concurrency import execute,verify,settle
from src.services.shifts import ensure_shift_global
ensure_shift_global()
while True:
    Path('/tmp/worker-heartbeat').touch()
    item=queue.blpop('locklab:queue',timeout=2)
    if not item: continue
    ident=int(item[1])
    try:
        facts=execute(ident)
        result=verify(ident,facts)
        try: Path(f'/artifacts/trial-{ident}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        except Exception: traceback.print_exc()  # 产物写失败不影响已落库的核验结论
        try: settle(ident)
        except Exception: traceback.print_exc()  # 入账失败留在待补记，可重试
    except Exception as e:
        traceback.print_exc()
        with Session() as db:
            obj=db.get(Trial,ident)
            if obj: obj.status='failed';obj.error=str(e);db.commit()
