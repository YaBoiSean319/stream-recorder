"""Public recording titles and safe download names, independent of media paths."""
import datetime as dt
import re
import unicodedata
from urllib.parse import quote


def validate_title(value):
    if not isinstance(value, str):
        raise ValueError('title must be text between 1 and 200 characters')
    title = value.strip()
    if not title or len(title) > 200 or any(unicodedata.category(c)[0] == 'C' for c in title):
        raise ValueError('title must be 1–200 characters without control or invisible characters')
    return title


def recording_title(record):
    if record.get('title'):
        return record['title']
    try:
        started = dt.datetime.fromisoformat(record['started_at']).astimezone(dt.timezone.utc)
        return 'Recording ' + started.strftime('%Y-%m-%d %H:%M UTC')
    except (KeyError, ValueError, TypeError):
        return 'Recording ' + record['id'][:8]


def download_filename(record):
    if not record.get('title') and record.get('filename'):
        return record['filename']
    title = unicodedata.normalize('NFKC', recording_title(record))
    safe = ''.join(c if unicodedata.category(c)[0] in {'L', 'N', 'M'} or c in ' -_.()' else '-' for c in title)
    safe = re.sub(r'\s+', ' ', safe).strip(' .-_')
    # Bound UTF-8 bytes as well as code points for common local filesystems.
    safe = safe[:100]
    while len((safe + '.mp4').encode('utf-8')) > 180:
        safe = safe[:-1]
    safe = safe.rstrip(' .-_')
    if not safe or safe.upper().split('.')[0] in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        safe = 'Recording-' + record['id'][:8]
    return safe + '.mp4'


def content_disposition(filename):
    ascii_name = re.sub(r'[^A-Za-z0-9 ._()-]', '_', filename).strip(' .') or 'recording.mp4'
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename, safe="")}'
