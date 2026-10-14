"""Offline local administrator recovery; run only with the application stopped."""
import getpass
import json
import secrets
import sqlite3
import sys
from pathlib import Path
from recorder.auth import password_hash

def main():
    path = Path(sys.argv[1] if len(sys.argv)>1 else '/data') / 'state.sqlite'
    if not path.is_file():
        raise SystemExit('No recorder database at the selected data directory')
    db = sqlite3.connect(path)
    row = db.execute('SELECT payload FROM auth_state WHERE id=1').fetchone()
    if not row:
        raise SystemExit('No configured authentication state')
    state = json.loads(row[0])
    username = input('Local administrator username: ').strip()
    password = getpass.getpass('New password (12-1024 characters): ')
    if not username or not 12 <= len(password) <= 1024 or password != getpass.getpass('Repeat password: '):
        raise SystemExit('Invalid username/password or passwords differ')
    salt = secrets.token_hex(16)
    state['admin'] = {'username':username, 'salt':salt, 'hash':password_hash(password,salt)}
    db.execute('UPDATE auth_state SET payload=? WHERE id=1',(json.dumps(state),))
    db.commit()
    db.close()
    print('Local administrator reset; restart the recorder.')

if __name__ == '__main__':
    main()
