from app.errors import DataError
from app.evidence import finding
from .reader import decode, tolerant_records


def intake(payload):
    text, encoding = decode(payload)
    if not text:
        raise DataError('EMPTY','文件无内容')
    if not text.strip():
        raise DataError('BLANK_FILE','文件只有空白')
    header = None
    rejected, accepted, blanks, repeat = [],[],[],[]
    rows = tolerant_records(text)
    truncated = None
    while True:
        try:
            line, raw = next(rows)
        except StopIteration as stopped:
            truncated = stopped.value
            break
        values = [part.strip().removeprefix('﻿') for part in raw]
        if header is None:
            if not any(values):
                continue
            header = values
            if not all(header) or len(set(header)) != len(header):
                raise DataError('HEADER','表头存在空列或重复字段')
            continue
        if not any(values):
            blanks.append(line)
        elif values == header:
            repeat.append(line)
        elif len(values) != len(header):
            rejected.append(finding({'line':line,'raw':raw}, 'WIDTH', f'{len(values)} 列，应为 {len(header)} 列'))
        else:
            accepted.append({'line':line,'raw':raw,'values':values})
    if header is None:
        # 截断发生在表头之前时无任何可采信记录，仍按整批拒绝处理
        if truncated is not None:
            raise DataError('CSV',f'第 {truncated["line"]} 行表格语法错误')
        raise DataError('BLANK_FILE','文件只有空白')
    tail = []
    if truncated is not None:
        tail = [finding({'line':truncated['line'],'raw':[truncated['text'].rstrip('\r\n')]},
                        'TAIL_TRUNCATED','末行未读完，批次暂算待补，未读记录不得采信')]
        tail[0]['prefix'] = truncated['prefix']
    count = sum(map(len,(rejected, accepted, blanks, repeat, tail)))
    if not count:
        raise DataError('HEADER_ONLY','文件只有表头')
    return {'header':header,'encoding':encoding,'rows':accepted,'rejected':rejected,'blank_lines':blanks,
            'header_lines':repeat,'truncated':tail,'input_count':count}
