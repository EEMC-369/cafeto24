import sqlite3, glob, sys
sys.stdout.reconfigure(encoding='utf-8')

dbs = glob.glob('*.db') + glob.glob('CajaCafeto24.dist/*.db')
print('DB files found:', dbs)

for db in dbs[:3]:
    try:
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='ventas'"
        ).fetchone()
        if row:
            print(f'--- {db} ---')
            print(row[0])
        conn.close()
    except Exception as e:
        print('Error:', e)
