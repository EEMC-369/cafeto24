import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect('cafeteria.db')
conn.row_factory = sqlite3.Row

# Ver entradas en caja_movimientos con metodo dividido
rows = conn.execute("""
    SELECT id, turno_id, tipo_movimiento, origen, descripcion, metodo_pago, monto
    FROM caja_movimientos
    WHERE metodo_pago = 'dividido' OR descripcion LIKE '%dividido%'
    ORDER BY id DESC LIMIT 20
""").fetchall()
print('=== caja_movimientos con dividido ===')
for r in rows:
    print(dict(r))

# Ver ultimas ventas con metodo dividido
rows2 = conn.execute("""
    SELECT id, turno_id, metodo_pago, total FROM ventas
    WHERE metodo_pago = 'dividido' ORDER BY id DESC LIMIT 10
""").fetchall()
print()
print('=== ventas con dividido ===')
for r in rows2:
    print(dict(r))

# Ver todos los metodos distintos en caja_movimientos
rows3 = conn.execute("""
    SELECT metodo_pago, tipo_movimiento, origen, COUNT(*) as cnt, SUM(monto) as total
    FROM caja_movimientos
    GROUP BY metodo_pago, tipo_movimiento, origen
    ORDER BY metodo_pago
""").fetchall()
print()
print('=== Todos los metodos en caja_movimientos ===')
for r in rows3:
    print(dict(r))

conn.close()
