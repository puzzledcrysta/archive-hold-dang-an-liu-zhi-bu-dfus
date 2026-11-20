"""末行截断批次的暂算与补传规则。"""
from base64 import b64encode
import csv
import io
import pytest


def cut_inside_last_record(payload: bytes, keep_physical: int) -> bytes:
    """保留前 keep_physical 个物理行，并把下一物理行切成未闭合的半截记录。"""
    lines = payload.decode('utf-8-sig').splitlines(keepends=True)
    head = ''.join(lines[:keep_physical])
    partial_next = lines[keep_physical].split(',', 1)[0] + ',"传输中断'
    return (head + partial_next).encode()


@pytest.fixture
def truncated_source(source):
    # 保留表头和前 5 个物理行，第 6 个物理行切成未读半截
    return cut_inside_last_record(source, 5)


def run_truncated(client, truncated_source, book):
    return client.post('/api/runs', json={'data_base64': b64encode(truncated_source).decode(),
                                          'book_base64': b64encode(book).decode()})


def test_truncated_run_is_provisional_and_keeps_readable_prefix(client, truncated_source, book):
    report = run_truncated(client, truncated_source, book).json()
    assert report['provisional'] is True
    assert report['manifest']['status'] == 'PROVISIONAL'
    assert report['truncated'][0]['code'] == 'TAIL_TRUNCATED'
    assert report['counts']['truncated'] == 1
    # 前半份完整记录仍可当天暂算对账
    assert report['counts']['accepted'] >= 1
    assert report['balanced'] is True


def test_provisional_match_cannot_be_confirmed(client, truncated_source, book):
    run_id = run_truncated(client, truncated_source, book).json()['manifest']['run_id']
    # 即使暂算记录碰巧与外部账相符，也不能核清送去确认
    response = client.post(f'/api/review/{run_id}',
                           json={'version': 0, 'decision': '确认', 'note': '暂算看似相符'})
    assert (response.status_code, response.json()['code']) == (409, 'PROVISIONAL')
    assert client.get(f'/api/review/{run_id}').json() == []


def test_completion_restores_formal_reconciliation(client, source, truncated_source, book):
    provisional_id = run_truncated(client, truncated_source, book).json()['manifest']['run_id']
    completed = client.post(f'/api/runs/{provisional_id}/complete',
                            json={'data_base64': b64encode(source).decode()})
    assert completed.status_code == 200
    formal = completed.json()
    assert formal['provisional'] is False and formal['manifest']['status'] == 'FORMAL'
    assert formal['counts']['truncated'] == 0
    # 正式批次与“原文件一开始就完整”的批次完全一致
    fresh = client.post('/api/runs', json={'data_base64': b64encode(source).decode(),
                                           'book_base64': b64encode(book).decode()}).json()
    assert formal == fresh
    # 正式批次可以复核确认
    ok = client.post(f'/api/review/{formal["manifest"]["run_id"]}',
                     json={'version': 0, 'decision': '确认', 'note': '补齐后核清'})
    assert ok.status_code == 200


def test_completion_that_changes_complete_records_is_refused(client, source, truncated_source, book):
    provisional_id = run_truncated(client, truncated_source, book).json()['manifest']['run_id']
    text = source.decode('utf-8-sig').replace('2026-11-18T01:30:00Z', '2026-11-18T02:30:00Z', 1).encode()
    response = client.post(f'/api/runs/{provisional_id}/complete',
                           json={'data_base64': b64encode(text).decode()})
    assert (response.status_code, response.json()['code']) == (422, 'SUPPLEMENT_CHANGED')
    # 批次仍待补：暂算批次依旧是暂算，且没有生成新批次
    assert client.get(f'/api/runs/{provisional_id}').json()['provisional'] is True


def test_still_truncated_supplement_keeps_batch_pending(client, truncated_source, book):
    provisional_id = run_truncated(client, truncated_source, book).json()['manifest']['run_id']
    longer = truncated_source + ' 仍未闭合'.encode()
    response = client.post(f'/api/runs/{provisional_id}/complete',
                           json={'data_base64': b64encode(longer).decode()})
    assert (response.status_code, response.json()['code']) == (422, 'SUPPLEMENT_TRUNCATED')
    assert client.get(f'/api/runs/{provisional_id}').json()['provisional'] is True


def test_same_supplement_rerun_creates_no_new_batch(client, truncated_source, book):
    first = run_truncated(client, truncated_source, book)
    run_id = first.json()['manifest']['run_id']
    # 用完全相同的截断字节重跑补传：幂等返回原暂算批次，不新增批次
    again = client.post(f'/api/runs/{run_id}/complete',
                        json={'data_base64': b64encode(truncated_source).decode()})
    assert again.status_code == 200 and again.json() == first.json()
    ids = {item['id'] for item in client.get('/api/runs').json()}
    assert ids == {run_id}


def test_complete_only_accepts_provisional_batch(client, source):
    formal_id = client.post('/api/runs', json={}).json()['manifest']['run_id']
    response = client.post(f'/api/runs/{formal_id}/complete',
                           json={'data_base64': b64encode(source).decode()})
    assert (response.status_code, response.json()['code']) == (422, 'SUPPLEMENT_STATE')


def test_completion_that_drops_read_records_is_refused(client, truncated_source, book):
    provisional_id = run_truncated(client, truncated_source, book).json()['manifest']['run_id']
    shorter = truncated_source.decode().splitlines(keepends=True)[0].encode()  # 只剩表头
    response = client.post(f'/api/runs/{provisional_id}/complete',
                           json={'data_base64': b64encode(shorter).decode()})
    assert response.status_code != 200
    assert client.get(f'/api/runs/{provisional_id}').json()['provisional'] is True


def test_history_marks_provisional_and_completion(client, source, truncated_source, book):
    provisional_id = run_truncated(client, truncated_source, book).json()['manifest']['run_id']
    formal_id = client.post(f'/api/runs/{provisional_id}/complete',
                            json={'data_base64': b64encode(source).decode()}).json()['manifest']['run_id']
    history = {item['id']: item for item in client.get('/api/runs').json()}
    assert history[provisional_id]['status'] == 'PROVISIONAL'
    assert history[provisional_id]['completed'] is True
    assert history[formal_id]['status'] == 'FORMAL' and history[formal_id]['completed'] is False


def test_export_flags_provisional_batch(client, truncated_source, book):
    run_id = run_truncated(client, truncated_source, book).json()['manifest']['run_id']
    evidence = client.get(f'/api/export/{run_id}').json()
    assert evidence['provisional'] is True
    assert evidence['truncated'][0]['code'] == 'TAIL_TRUNCATED'


def test_multiline_unclosed_tail_uses_exact_prefix(client, book):
    # 未读尾部自身跨物理行（引号内换行）时，已读前缀仍须逐字节受保护
    header = ['档案件','修订号','提交时间','结案日','查询日','保管政策','留置区段','原始备注']
    row1 = ['00101','1','2026-11-18T01:30:00Z','2024-02-29','2025-03-02',
            '2024-01-01/2025-01-01/1','无','引号内\n换行备注']
    row2 = ['00102','1','2026-11-18T01:30:00Z','2024-02-29','2025-03-02',
            '2024-01-01/2025-01-01/1','无','第二条']
    full_buf = io.StringIO(newline='')
    csv.writer(full_buf).writerows([header, row1, row2])
    full = full_buf.getvalue()
    lines = full.splitlines(keepends=True)
    partial = ''.join(lines[:-1]) + lines[-1].split(',', 1)[0] + ',"未闭合\n跨物理行'
    provisional = client.post('/api/runs', json={'data': partial}).json()
    assert provisional['provisional'] is True
    pid = provisional['manifest']['run_id']
    completed = client.post(f'/api/runs/{pid}/complete', json={'data': full})
    assert completed.status_code == 200 and completed.json()['provisional'] is False
    changed = client.post(f'/api/runs/{pid}/complete',
                          json={'data': full.replace('换行备注', '换行备注X')})
    assert (changed.status_code, changed.json()['code']) == (422, 'SUPPLEMENT_CHANGED')


def test_completion_with_unread_newer_revision_formalized(client, source, book):
    # 未读尾部可能是同档案件的更新版：补齐后正式对账必须以补齐文件为准重新分版
    reader = csv.reader(io.StringIO(source.decode('utf-8-sig')))
    header, old = next(reader), next(reader)
    newer = old.copy()
    newer[1] = '9'
    full = io.StringIO(newline='')
    csv.writer(full).writerows([header, old, newer])
    full_bytes = full.getvalue().encode()
    partial = cut_inside_last_record(full_bytes, 2)  # 更新版记录切成未读半截
    provisional = client.post('/api/runs',
                              json={'data_base64': b64encode(partial).decode(),
                                    'book_base64': b64encode(book).decode()}).json()
    assert provisional['provisional'] is True
    formal = client.post(f'/api/runs/{provisional["manifest"]["run_id"]}/complete',
                         json={'data_base64': b64encode(full_bytes).decode()}).json()
    assert formal['provisional'] is False
    assert {row['key']: row['revision'] for row in formal['accepted']} == {old[0]: 9}
