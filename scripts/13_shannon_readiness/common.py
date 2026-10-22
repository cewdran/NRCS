"""Standard-library I/O helpers; scientific entry points invoke the Puma guard first."""
from pathlib import Path
import csv
import hashlib
import json


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            digest.update(block)
    return digest.hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def table(path, rows, fields=None):
    path = Path(path)
    rows = list(rows)
    columns = list(fields or ())
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    require(columns, 'An empty table requires an explicit schema: ' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def readcsv(path):
    with Path(path).open(newline='', encoding='utf-8-sig') as stream:
        return list(csv.DictReader(stream))


def truth(value):
    return value is True or str(value).strip().lower() in ('true', '1', 'yes')


def wbrows(table_value):
    return [dict(zip(table_value['columns'], row)) for row in table_value['rows']]
