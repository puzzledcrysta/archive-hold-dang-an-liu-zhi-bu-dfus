import csv
from io import StringIO
from app.errors import DataError

TRUNCATED_MESSAGE = 'unexpected end of data'


def decode(payload):
    for encoding in ('utf-8-sig','gb18030'):
        try:
            return payload.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise DataError('ENCODING','只接受 UTF-8 或 GB18030')


def tolerant_records(text):
    """逐行解析，末行引号截断时返回未读完的物理尾部而不是整批拒绝。

    产出 (line, row)；返回 None 表示文件完整，返回字典表示存在截断尾部，
    其中 prefix 是已成功读完的精确文本前缀，tail 是未读尾部原文。
    其他表格语法错误仍按整批拒绝处理。
    """
    stream = StringIO(text, newline='')
    reader = csv.reader(stream,strict=True)
    consumed = 0
    try:
        while True:
            line = reader.line_num + 1
            row = next(reader)
            # csv 读完一条记录后，底层流恰好停在下一物理行起点
            consumed = stream.tell()
            yield line, row
    except StopIteration:
        return None
    except csv.Error as error:
        if str(error) != TRUNCATED_MESSAGE:
            raise DataError('CSV',f'第 {reader.line_num} 行表格语法错误') from error
    return {'line': reader.line_num, 'prefix': text[:consumed], 'text': text[consumed:]}


def records(text):
    iterator = tolerant_records(text)
    while True:
        try:
            yield next(iterator)
        except StopIteration as stopped:
            if stopped.value is not None:
                raise DataError('CSV',f'第 {stopped.value["line"]} 行表格语法错误')
            return
