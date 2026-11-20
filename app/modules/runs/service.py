import json
from datetime import datetime, timezone
from time import perf_counter
from app.errors import DataError
from app.manifest import manifest
from app.pipeline import execute
from app.modules.intake.reader import decode
from .repository import fetch, persist, link_completion


def run(source,book):
    proof = manifest(source,book)
    started = perf_counter()
    result = execute(source,book)
    created = datetime.now(timezone.utc).isoformat()
    proof.update(elapsed_ms=round((perf_counter()-started)*1000),created=created,
                 status='PROVISIONAL' if result['provisional'] else 'FORMAL')
    result['manifest'] = proof
    stored = persist(proof['run_id'],source,book,result,created)
    return json.loads(stored['result'])


def replay(run_id):
    old = fetch(run_id)
    return run(old['source'],old['book'])


def get(run_id):
    return json.loads(fetch(run_id)['result'])


def complete(run_id, source):
    """用补传文件补齐暂算批次。

    补传必须逐字节保留暂算批次已读完的全部内容，只允许在其后补出原未读尾部；
    一旦先前完整记录被改动，批次仍待补。同一补传重跑不新增批次。
    """
    old = fetch(run_id)
    previous = json.loads(old['result'])
    if not previous.get('provisional'):
        raise DataError('SUPPLEMENT_STATE','该批次不是暂算批次，无需补传')
    text, _ = decode(source)
    if source == old['source']:
        # 重跑同一补传（仍是原截断字节）：幂等返回，不新增批次
        return previous
    completed = execute(source, old['book'])
    if completed['provisional']:
        raise DataError('SUPPLEMENT_TRUNCATED','补传文件仍有末行截断，批次仍待补')
    # 暂算批次已读完的精确文本前缀，逐字节不得改动（含引号内换行的多行记录）
    prefix = previous['truncated'][0]['prefix']
    if not text.startswith(prefix):
        raise DataError('SUPPLEMENT_CHANGED','补传改动了先前完整的记录，批次仍待补')
    result = run(source, old['book'])
    link_completion(run_id, result['manifest']['run_id'])
    return result
