import sqlite3

db = sqlite3.connect('cafeteria.db')
db.row_factory = sqlite3.Row

turnos_recientes = db.execute("""
    SELECT id, fecha_apertura, estado, COALESCE(nombre_usuario, 'Cajero') as nombre_usuario
    FROM turnos
    ORDER BY id DESC
    LIMIT 7
""").fetchall()

ventas = db.execute("""
    SELECT
        'venta' as tipo,
        id as registro_id,
        turno_id,
        fecha,
        COALESCE(metodo_pago, 'efectivo') as metodo_pago,
        total as monto,
        CASE
            WHEN COALESCE(tipo_venta, 'producto') = 'intangible' THEN
                COALESCE('Intangible #' || id || ' - ' || referencia_pago, 'Intangible #' || id)
            ELSE
                COALESCE('Venta #' || id, 'Venta')
        END as etiqueta
    FROM ventas
    WHERE COALESCE(metodo_pago, 'efectivo') != 'fiado'
    ORDER BY id DESC
    LIMIT 50
""").fetchall()

gastos = db.execute("""
    SELECT
        'gasto' as tipo,
        id as registro_id,
        turno_id,
        fecha,
        COALESCE(metodo_pago, 'efectivo') as metodo_pago,
        monto,
        COALESCE('Gasto #' || id || ' - ' || descripcion, 'Gasto') as etiqueta
    FROM gastos
    ORDER BY id DESC
    LIMIT 50
""").fetchall()

abonos = db.execute("""
    SELECT
        'abono' as tipo,
        id as registro_id,
        turno_id,
        fecha,
        COALESCE(metodo_pago, 'efectivo') as metodo_pago,
        monto,
        COALESCE('Abono #' || id || ' - ' || cliente_nombre, 'Abono') as etiqueta
    FROM abonos_deuda
    ORDER BY id DESC
    LIMIT 50
""").fetchall()

movimientos = [dict(x) for x in ventas] + [dict(x) for x in gastos] + [dict(x) for x in abonos]
movimientos.sort(key=lambda x: (x.get('fecha') or '', int(x.get('registro_id') or 0)), reverse=True)

print("Turnos encontrados:", len(turnos_recientes))
print("Total movimientos:", len(movimientos))
if movimientos:
    print("Primer movimiento:", movimientos[0])
db.close()
