"""I/O helpers. Scientific entry points call require_hpc before importing these."""
from pathlib import Path
import csv, json, hashlib, gzip, math

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1048576), b''):
            h.update(block)
    return h.hexdigest()

def save(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+'\n')

def table(path, rows, fields=None):
    fields = fields or (list(rows[0]) if rows else ['status'])
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

def readcsv(path):
    with Path(path).open(newline='') as handle:
        return list(csv.DictReader(handle))

def wbrows(t):
    return [dict(zip(t['columns'], row)) for row in t['rows']]

def truth(x):
    return x is True or str(x).lower() in ('true', '1', 'yes')

def counts(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    data = {}
    with opener(path, 'rt') as handle:
        reader = csv.reader(handle, delimiter='\t')
        header = next(reader)
        ids = header[1:]
        require(len(ids) == len(set(ids)), 'Duplicate count ASV IDs')
        for row in reader:
            require(len(row) == len(header), 'Ragged count row')
            require(row[0] not in data, 'Duplicate count sample: '+row[0])
            vals = [float(v) for v in row[1:]]
            require(all(math.isfinite(v) and v >= 0 and v.is_integer() for v in vals), 'Invalid integer counts')
            data[row[0]] = [int(v) for v in vals]
    return ids, data

def fasta(path):
    seqs, name, parts = {}, None, []
    def finish():
        if name is not None:
            require(name not in seqs, 'Duplicate FASTA ID '+name)
            seq = ''.join(parts).upper()
            require(bool(seq) and set(seq) <= set('ACGTRYSWKMBDHVN'), 'Invalid sequence '+name)
            seqs[name] = seq
    with Path(path).open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            if line.startswith('>'):
                finish()
                name = line[1:].split()[0]
                parts = []
            else:
                require(name is not None, 'Sequence before FASTA header')
                parts.append(line)
    finish()
    return seqs
