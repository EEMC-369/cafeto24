import sqlite3
import os
import json
import shutil
from datetime import datetime
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    jsonify,
    flash,
    send_from_directory
)
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib import colors


import sys

# Determinar si la aplicación se ejecuta congelada (.exe) mediante PyInstaller o Nuitka
es_compilado = getattr(sys, 'frozen', False) or '__compiled__' in globals() or 'nuitka' in sys.modules

if es_compilado:
    # Si usa PyInstaller Onefile, los recursos están en _MEIPASS
    if hasattr(sys, '_MEIPASS'):
        RESOURCE_DIR = sys._MEIPASS
    else:
        # Nuitka Standalone / PyInstaller Standalone
        RESOURCE_DIR = os.path.dirname(sys.executable)
        
    # En Windows compilado, la base de datos se guarda en C:\ProgramData\Cafeto24
    # para evitar errores de permisos al escribir en C:\Program Files
    USER_DATA_DIR = os.path.join(os.environ.get('ALLUSERSPROFILE', 'C:\\ProgramData'), 'Cafeto24')
    if not os.path.exists(USER_DATA_DIR):
        try:
            os.makedirs(USER_DATA_DIR, exist_ok=True)
        except Exception:
            # Fallback en caso de problemas raros de permisos en ProgramData
            USER_DATA_DIR = os.path.dirname(sys.executable)
            
    # Redirigir stdout y stderr a un archivo de log en ProgramData
    try:
        log_file = open(os.path.join(USER_DATA_DIR, 'debug_error.log'), 'a', encoding='utf-8', buffering=1)
        sys.stdout = log_file
        sys.stderr = log_file
        print("\n--- INICIO DE APLICACION COMPILADA ---")
    except Exception:
        pass
else:
    RESOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
    USER_DATA_DIR = os.path.dirname(os.path.abspath(__file__))

VERSION = "3.14.0"

app = Flask(
    __name__,
    template_folder=os.path.join(RESOURCE_DIR, 'templates'),
    static_folder=os.path.join(RESOURCE_DIR, 'static')
)
app.secret_key = 'clave_secreta_para_sesiones_cafeto'

puerto = 8080
host = "0.0.0.0"

@app.context_processor
def inject_network_info():
    global puerto, host
    ip_local = "127.0.0.1"
    if host == "0.0.0.0":
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip_local = s.getsockname()[0]
            s.close()
        except Exception:
            try:
                ip_local = socket.gethostbyname(socket.gethostname())
            except Exception:
                ip_local = "127.0.0.1"
    else:
        ip_local = host
    
    return {
        'ip_local': ip_local,
        'puerto_actual': puerto,
        'url_acceso_red': f"http://{ip_local}:{puerto}"
    }

# ==========================================
# 🔐 CONTROL DE LICENCIAS POR HARDWARE (NODE-LOCKED)
# ==========================================
import licencia_utils

# Cache en memoria para evitar accesos repetitivos a disco y PowerShell en cada petición
_licencia_valida_cache = None

def verificar_licencia_cache():
    global _licencia_valida_cache
    if _licencia_valida_cache is True:
        return True
        
    lic_path = os.path.join(USER_DATA_DIR, "licencia.key")
    if not os.path.exists(lic_path):
        _licencia_valida_cache = False
        return False
        
    try:
        with open(lic_path, 'r', encoding='utf-8') as f:
            lic_hex = f.read().strip()
        machine_id = licencia_utils.obtener_machine_id()
        es_valido, msg = licencia_utils.validar_licencia(lic_hex, machine_id)
        _licencia_valida_cache = es_valido
        return es_valido
    except Exception:
        _licencia_valida_cache = False
        return False

@app.before_request
def bloquear_por_licencia():
    # Permitir libre acceso a la ruta de activación, archivos estáticos y deslogueo
    if request.path == '/licencia' or request.path.startswith('/static/') or request.path == '/logout':
        return

    if not verificar_licencia_cache():
        return redirect(url_for('pantalla_licencia'))

    # Validar sesión de turno activa para evitar desajustes de base de datos
    try:
        from flask import session
        if 'usuario' in session and session.get('id'):
            obtener_turno_activo(session.get('id'))
    except Exception:
        pass

@app.route('/licencia', methods=['GET', 'POST'])
def pantalla_licencia():
    global _licencia_valida_cache
    machine_id = licencia_utils.obtener_machine_id()
    
    if request.method == 'POST':
        lic_hex = request.form.get('licencia_key', '').strip()
        es_valido, msg = licencia_utils.validar_licencia(lic_hex, machine_id)
        if es_valido:
            lic_path = os.path.join(USER_DATA_DIR, "licencia.key")
            try:
                with open(lic_path, 'w', encoding='utf-8') as f:
                    f.write(lic_hex)
                _licencia_valida_cache = True
                flash("Sistema activado correctamente. ¡Bienvenido a Cafeto24!", "success")
                return redirect(url_for('login'))
            except Exception as e:
                flash(f"Error al guardar la licencia en disco: {e}", "error")
        else:
            flash(f"Error de activación: {msg}", "error")
            
    return render_template('licencia.html', machine_id=machine_id)

def conectar_db():
    # timeout=10 evita errores de bloqueo si el administrador consulta datos
    # mientras el cajero procesa una venta concurrentemente.
    db_path = os.path.join(USER_DATA_DIR, "cafeteria.db")
    conexion = sqlite3.connect(db_path, timeout=10)
    conexion.row_factory = sqlite3.Row
    return conexion


def obtener_turno_activo(usuario_id):
    db = conectar_db()
    try:
        # Validar si el turno de la sesion aun es valido en la base de datos
        try:
            from flask import session, request
            if request:
                session_turno_id = session.get('turno_id')
                if session_turno_id:
                    turno_val = db.execute("""
                        SELECT id FROM turnos 
                        WHERE id = ? AND usuario_id = ? AND estado = 'abierto'
                    """, (session_turno_id, usuario_id)).fetchone()
                    if not turno_val:
                        session.pop('turno_id', None)
        except Exception:
            pass

        turno = db.execute("""
            SELECT id
            FROM turnos
            WHERE usuario_id = ? AND estado = 'abierto'
            ORDER BY id DESC
            LIMIT 1
        """, (usuario_id,)).fetchone()
        return int(turno['id']) if turno else None
    finally:
        db.close()


def abrir_turno(usuario):
    db = conectar_db()
    try:
        turno_existente = db.execute("""
            SELECT id
            FROM turnos
            WHERE usuario_id = ? AND estado = 'abierto'
            ORDER BY id DESC
            LIMIT 1
        """, (usuario['id'],)).fetchone()

        if turno_existente:
            return turno_existente['id']

        cursor = db.execute("""
            INSERT INTO turnos (usuario_id, nombre_usuario, rol, fecha_apertura, estado)
            VALUES (?, ?, ?, DATETIME('now', 'localtime'), 'abierto')
        """, (usuario['id'], usuario['nombre'], usuario['rol']))
        db.commit()
        return cursor.lastrowid
    finally:
        db.close()


def cerrar_turno(
    turno_id=None, usuario_id=None,
    efectivo_esperado=None, efectivo_real=None, diferencia=None,
    nequi_esperado=None, nequi_real=None, diferencia_nequi=None,
    daviplata_esperado=None, daviplata_real=None, diferencia_daviplata=None,
    tarjeta_esperado=None, tarjeta_real=None, diferencia_tarjeta=None,
    observaciones=None
):
    if not turno_id and not usuario_id:
        return

    db = conectar_db()
    try:
        if not turno_id and usuario_id:
            turno = db.execute("""
                SELECT id
                FROM turnos
                WHERE usuario_id = ? AND estado = 'abierto'
                ORDER BY id DESC
                LIMIT 1
            """, (usuario_id,)).fetchone()
            if turno:
                turno_id = turno['id']

        if turno_id:
            db.execute("""
                UPDATE turnos
                SET estado = 'cerrado',
                    fecha_cierre = COALESCE(fecha_cierre, DATETIME('now', 'localtime')),
                    efectivo_esperado = ?, efectivo_real = ?, diferencia = ?,
                    nequi_esperado = ?, nequi_real = ?, diferencia_nequi = ?,
                    daviplata_esperado = ?, daviplata_real = ?, diferencia_daviplata = ?,
                    tarjeta_esperado = ?, tarjeta_real = ?, diferencia_tarjeta = ?,
                    observaciones = ?
                WHERE id = ? AND estado = 'abierto'
            """, (
                efectivo_esperado, efectivo_real, diferencia,
                nequi_esperado, nequi_real, diferencia_nequi,
                daviplata_esperado, daviplata_real, diferencia_daviplata,
                tarjeta_esperado, tarjeta_real, diferencia_tarjeta,
                observaciones, turno_id
            ))
            db.commit()
    finally:
        db.close()


def registrar_movimiento_caja(db, turno_id, usuario_id, tipo_movimiento, origen, descripcion, metodo_pago, monto):
    db.execute("""
        INSERT INTO caja_movimientos (
            turno_id,
            usuario_id,
            tipo_movimiento,
            origen,
            descripcion,
            metodo_pago,
            monto,
            fecha
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, DATETIME('now', 'localtime'))
    """, (turno_id, usuario_id, tipo_movimiento, origen, descripcion, metodo_pago, monto))


def _obtener_directorio_reportes_cierre():
    reportes_dir = os.path.join(USER_DATA_DIR, 'reportes_cierre')
    os.makedirs(reportes_dir, exist_ok=True)
    return reportes_dir


def _extraer_concepto_referencia_intangible(referencia_pago):
    referencia = (referencia_pago or '').strip()
    if '|' in referencia:
        concepto, ref = referencia.split('|', 1)
        return concepto.strip() or 'Ingreso intangible', ref.strip()
    return (referencia or 'Ingreso intangible'), ''


def generar_archivos_cierre_turno(turno_id):
    db = conectar_db()
    try:
        turno = db.execute("""
            SELECT id, nombre_usuario, rol, fecha_apertura, 
                   COALESCE(fecha_cierre, DATETIME('now', 'localtime')) as fecha_cierre,
                   efectivo_esperado, efectivo_real, diferencia,
                   nequi_esperado, nequi_real, diferencia_nequi,
                   daviplata_esperado, daviplata_real, diferencia_daviplata,
                   tarjeta_esperado, tarjeta_real, diferencia_tarjeta,
                   observaciones
            FROM turnos
            WHERE id = ?
        """, (turno_id,)).fetchone()

        if not turno:
            raise ValueError('No se encontró el turno para generar cierre')

        resumen_turno = db.execute("""
            SELECT
                COUNT(id) as ordenes,
                COALESCE(SUM(total), 0) as total_ventas
            FROM ventas
            WHERE turno_id = ?
        """, (turno_id,)).fetchone()

        ventas_tangibles = db.execute("""
            SELECT
                COALESCE(c.nombre_categoria, 'Sin categoría') as categoria,
                COALESCE(p.nombre, 'Producto') as producto,
                COALESCE(SUM(dv.cantidad), 0) as cantidad,
                COALESCE(SUM(dv.cantidad * dv.precio_unitario), 0) as total
            FROM detalle_ventas dv
            JOIN ventas v ON v.id = dv.venta_id
            LEFT JOIN productos p ON p.id = dv.producto_id
            LEFT JOIN categorias c ON c.id = p.categoria_id
            WHERE v.turno_id = ?
            GROUP BY categoria, producto
            ORDER BY categoria ASC, producto ASC
        """, (turno_id,)).fetchall()

        ventas_intangibles = db.execute("""
            SELECT
                referencia_pago,
                COUNT(id) as cantidad,
                COALESCE(SUM(total), 0) as total
            FROM ventas
            WHERE turno_id = ? AND COALESCE(tipo_venta, 'producto') = 'intangible'
            GROUP BY referencia_pago
            ORDER BY total DESC
        """, (turno_id,)).fetchall()

        metodos = ['efectivo', 'nequi', 'daviplata', 'tarjeta']
        resumen_metodos = {m: {'ventas': 0.0, 'abonos': 0.0, 'gastos': 0.0, 'saldo': 0.0} for m in metodos}

        ventas_metodo = db.execute("""
            SELECT COALESCE(metodo_pago, 'efectivo') as metodo_pago, COALESCE(SUM(total), 0) as total
            FROM ventas
            WHERE turno_id = ? AND COALESCE(metodo_pago, 'efectivo') != 'fiado'
            GROUP BY COALESCE(metodo_pago, 'efectivo')
        """, (turno_id,)).fetchall()

        abonos_metodo = db.execute("""
            SELECT COALESCE(metodo_pago, 'efectivo') as metodo_pago, COALESCE(SUM(monto), 0) as total
            FROM abonos_deuda
            WHERE turno_id = ?
            GROUP BY COALESCE(metodo_pago, 'efectivo')
        """, (turno_id,)).fetchall()

        gastos_metodo = db.execute("""
            SELECT COALESCE(metodo_pago, 'efectivo') as metodo_pago, COALESCE(SUM(monto), 0) as total
            FROM gastos
            WHERE turno_id = ?
            GROUP BY COALESCE(metodo_pago, 'efectivo')
        """, (turno_id,)).fetchall()

        for fila in ventas_metodo:
            metodo = str(fila['metodo_pago']).lower()
            if metodo not in resumen_metodos:
                resumen_metodos[metodo] = {'ventas': 0.0, 'abonos': 0.0, 'gastos': 0.0, 'saldo': 0.0}
            resumen_metodos[metodo]['ventas'] = float(fila['total'] or 0)

        for fila in abonos_metodo:
            metodo = str(fila['metodo_pago']).lower()
            if metodo not in resumen_metodos:
                resumen_metodos[metodo] = {'ventas': 0.0, 'abonos': 0.0, 'gastos': 0.0, 'saldo': 0.0}
            resumen_metodos[metodo]['abonos'] = float(fila['total'] or 0)

        for fila in gastos_metodo:
            metodo = str(fila['metodo_pago']).lower()
            if metodo not in resumen_metodos:
                resumen_metodos[metodo] = {'ventas': 0.0, 'abonos': 0.0, 'gastos': 0.0, 'saldo': 0.0}
            resumen_metodos[metodo]['gastos'] = float(fila['total'] or 0)

        for metodo, valores in resumen_metodos.items():
            valores['saldo'] = valores['ventas'] + valores['abonos'] - valores['gastos']

        gastos_turno_raw = db.execute("""
            SELECT g.id, g.fecha, g.descripcion, g.categoria_gasto, COALESCE(g.metodo_pago, 'efectivo') as metodo_pago, g.monto
            FROM gastos g
            WHERE g.turno_id = ?
            ORDER BY g.id ASC
        """, (turno_id,)).fetchall()

        gastos_turno = []
        for g in gastos_turno_raw:
            d_str = str(g['descripcion'] or '').strip()
            cat_str = str(g['categoria_gasto'] or 'General').strip()
            
            if d_str.isdigit():
                p_row = db.execute("SELECT nombre FROM productos WHERE id = ?", (int(d_str),)).fetchone()
                if p_row:
                    d_str = f"Producto: {p_row['nombre']}"
                    if cat_str in ('General', 'Otros Gastos'):
                        cat_str = 'Compra Inventario'
                else:
                    i_row = db.execute("SELECT nombre_insumo FROM insumos WHERE id = ?", (int(d_str),)).fetchone()
                    if i_row:
                        d_str = f"Insumo: {i_row['nombre_insumo']}"
                        if cat_str in ('General', 'Otros Gastos'):
                            cat_str = 'Compra Insumo'

            gastos_turno.append({
                'id': g['id'],
                'fecha': g['fecha'],
                'descripcion': d_str,
                'categoria_gasto': cat_str,
                'metodo_pago': g['metodo_pago'],
                'monto': g['monto']
            })

        movimientos_turno = db.execute("""
            SELECT
                fecha,
                tipo_movimiento,
                origen,
                descripcion,
                COALESCE(metodo_pago, 'efectivo') as metodo_pago,
                monto
            FROM caja_movimientos
            WHERE turno_id = ?
            ORDER BY fecha ASC, id ASC
        """, (turno_id,)).fetchall()

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        nombre_base = f"turno_{turno_id}_{timestamp}"
        reportes_dir = _obtener_directorio_reportes_cierre()
        ruta_json = os.path.join(reportes_dir, f"cierre_{nombre_base}.json")
        ruta_pdf = os.path.join(reportes_dir, f"ventas_{nombre_base}.pdf")

        payload = {
            'turno': {
                'id': int(turno['id']),
                'usuario': turno['nombre_usuario'],
                'rol': turno['rol'],
                'fecha_apertura': turno['fecha_apertura'],
                'fecha_cierre': turno['fecha_cierre'],
                'efectivo_esperado': float(turno['efectivo_esperado'] or 0) if turno['efectivo_esperado'] is not None else None,
                'efectivo_real': float(turno['efectivo_real'] or 0) if turno['efectivo_real'] is not None else None,
                'diferencia': float(turno['diferencia'] or 0) if turno['diferencia'] is not None else None,
                'nequi_esperado': float(turno['nequi_esperado'] or 0) if turno['nequi_esperado'] is not None else None,
                'nequi_real': float(turno['nequi_real'] or 0) if turno['nequi_real'] is not None else None,
                'diferencia_nequi': float(turno['diferencia_nequi'] or 0) if turno['diferencia_nequi'] is not None else None,
                'daviplata_esperado': float(turno['daviplata_esperado'] or 0) if turno['daviplata_esperado'] is not None else None,
                'daviplata_real': float(turno['daviplata_real'] or 0) if turno['daviplata_real'] is not None else None,
                'diferencia_daviplata': float(turno['diferencia_daviplata'] or 0) if turno['diferencia_daviplata'] is not None else None,
                'tarjeta_esperado': float(turno['tarjeta_esperado'] or 0) if turno['tarjeta_esperado'] is not None else None,
                'tarjeta_real': float(turno['tarjeta_real'] or 0) if turno['tarjeta_real'] is not None else None,
                'diferencia_tarjeta': float(turno['diferencia_tarjeta'] or 0) if turno['diferencia_tarjeta'] is not None else None,
                'observaciones': turno['observaciones']
            },
            'resumen': {
                'ordenes': int(resumen_turno['ordenes'] or 0),
                'total_ventas': float(resumen_turno['total_ventas'] or 0)
            },
            'metodos': resumen_metodos,
            'gastos': [dict(g) for g in gastos_turno],
            'movimientos': [dict(m) for m in movimientos_turno]
        }

        with open(ruta_json, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        dibujar_pdf_reporte_z_completo(ruta_pdf, turno, resumen_turno, resumen_metodos, ventas_tangibles, ventas_intangibles, movimientos_turno, gastos_turno)

        return {
            'json': os.path.basename(ruta_json),
            'pdf': os.path.basename(ruta_pdf)
        }
    finally:
        db.close()


@app.route('/reportes_cierre/<path:nombre_archivo>')
def descargar_reporte_cierre(nombre_archivo):
    if 'usuario' not in session:
        return redirect(url_for('login'))

    directorio = _obtener_directorio_reportes_cierre()
    return send_from_directory(directorio, nombre_archivo, as_attachment=False)

def dibujar_pdf_reporte_z_completo(ruta_pdf, turno, resumen_turno, resumen_metodos, ventas_tangibles, ventas_intangibles, movimientos_turno, gastos_turno=None):
    if not isinstance(turno, dict):
        turno = dict(turno)
    if resumen_turno and not isinstance(resumen_turno, dict):
        resumen_turno = dict(resumen_turno)
    movimientos_turno = [dict(m) if not isinstance(m, dict) else m for m in (movimientos_turno or [])]
    gastos_turno = [dict(g) if not isinstance(g, dict) else g for g in (gastos_turno or [])]
    ventas_tangibles = [dict(v) if not isinstance(v, dict) else v for v in (ventas_tangibles or [])]
    ventas_intangibles = [dict(vi) if not isinstance(vi, dict) else vi for vi in (ventas_intangibles or [])]

    pdf = canvas.Canvas(ruta_pdf, pagesize=letter)
    width, height = letter
    margin = 36
    usable_width = width - (margin * 2)
    y = height - margin

    def check_page(needed=30):
        nonlocal y
        if y - needed < margin:
            pdf.showPage()
            y = height - margin

    # 1. TOP HEADER BANNER (Idéntico a Detalles de ventas.pdf)
    pdf.setFillColor(colors.HexColor('#F1F5F9'))
    pdf.rect(margin, y - 72, usable_width, 72, fill=1, stroke=0)

    pdf.setFillColor(colors.HexColor('#1E293B'))
    pdf.setFont('Helvetica-Bold', 11)
    pdf.drawString(margin + 12, y - 16, "CAFETO 24")
    pdf.setFont('Helvetica', 8)
    pdf.setFillColor(colors.HexColor('#64748B'))
    pdf.drawString(margin + 12, y - 28, "Diagonal 62 sur #22-04")
    pdf.drawString(margin + 12, y - 38, "Bogotá D.C., Colombia")
    pdf.drawString(margin + 12, y - 48, "NIT / IVA: 1013587664-8")

    pdf.setFillColor(colors.HexColor('#0F172A'))
    pdf.setFont('Helvetica-Bold', 14)
    pdf.drawRightString(width - margin - 12, y - 22, "Reporte diario de ventas Z")
    pdf.setFont('Helvetica', 8)
    pdf.setFillColor(colors.HexColor('#475569'))
    pdf.drawRightString(width - margin - 12, y - 38, f"ID de la sesión: CAFETO 24/{int(turno['id']):05d}")

    y -= 82

    # Rango de fechas
    pdf.setFillColor(colors.HexColor('#0F172A'))
    pdf.setFont('Helvetica', 8)
    f_ap = str(turno.get('fecha_apertura') or '')
    f_ci = str(turno.get('fecha_cierre') or 'Turno Activo')
    pdf.drawCentredString(width / 2, y, f"{f_ap}  -  {f_ci}")
    y -= 16

    # 2. SECCIÓN: VENTAS POR CATEGORÍA
    pdf.setFillColor(colors.HexColor('#E2E8F0'))
    pdf.rect(margin, y - 14, usable_width, 16, fill=1, stroke=0)
    pdf.setFillColor(colors.HexColor('#0F172A'))
    pdf.setFont('Helvetica-Bold', 9)
    pdf.drawString(margin + 8, y - 11, "VENTAS POR CATEGORÍA")
    y -= 22

    categorias_map = {}
    for item in ventas_tangibles:
        cat = item['categoria']
        if cat not in categorias_map:
            categorias_map[cat] = {'nombre': cat, 'cantidad': 0, 'total': 0, 'items': []}
        categorias_map[cat]['cantidad'] += float(item['cantidad'] or 0)
        categorias_map[cat]['total'] += float(item['total'] or 0)
        categorias_map[cat]['items'].append({
            'nombre': item['producto'],
            'cantidad': float(item['cantidad'] or 0),
            'total': float(item['total'] or 0)
        })

    for cat_name, cat in categorias_map.items():
        check_page(24)
        pdf.setFillColor(colors.HexColor('#1E293B'))
        pdf.setFont('Helvetica-Bold', 9)
        pdf.drawString(margin + 8, y, str(cat['nombre']))
        pdf.drawRightString(width - margin - 110, y, f"{cat['cantidad']:.1f}")
        pdf.drawRightString(width - margin - 8, y, f"${cat['total']:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
        y -= 4
        pdf.setStrokeColor(colors.HexColor('#CBD5E1'))
        pdf.setLineWidth(0.5)
        pdf.line(margin + 8, y, width - margin - 8, y)
        y -= 12

        for p in cat['items']:
            check_page(16)
            pdf.setFillColor(colors.HexColor('#475569'))
            pdf.setFont('Helvetica', 8)
            pdf.drawString(margin + 24, y, str(p['nombre']))
            pdf.drawRightString(width - margin - 110, y, f"{p['cantidad']:.1f} Unidades")
            pdf.drawRightString(width - margin - 8, y, f"${p['total']:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
            y -= 14
        y -= 4

    tot_ventas_z = float(resumen_turno['total_ventas'] or 0)
    check_page(24)
    pdf.setFillColor(colors.HexColor('#1E293B'))
    pdf.setFont('Helvetica-Bold', 9)
    pdf.drawString(margin + 8, y, "Total Ventas")
    pdf.drawRightString(width - margin - 8, y, f"${tot_ventas_z:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
    y -= 18

    # 3. SECCIÓN: PAGOS POR MÉTODO
    check_page(40)
    pdf.setFillColor(colors.HexColor('#E2E8F0'))
    pdf.rect(margin, y - 14, usable_width, 16, fill=1, stroke=0)
    pdf.setFillColor(colors.HexColor('#0F172A'))
    pdf.setFont('Helvetica-Bold', 9)
    pdf.drawString(margin + 8, y - 11, "PAGOS")
    y -= 22

    for metodo, valores in resumen_metodos.items():
        check_page(16)
        pdf.setFillColor(colors.HexColor('#334155'))
        pdf.setFont('Helvetica', 8)
        pdf.drawString(margin + 8, y, f"{metodo.capitalize()} CAFETO 24")
        pdf.drawRightString(width - margin - 8, y, f"${valores['ventas']:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
        y -= 14

    y -= 12

    # 4. SECCIÓN: CONTROL DE LA SESIÓN (ARQUEO MULTICUENTA)
    check_page(50)
    pdf.setFillColor(colors.HexColor('#E2E8F0'))
    pdf.rect(margin, y - 14, usable_width, 16, fill=1, stroke=0)
    pdf.setFillColor(colors.HexColor('#0F172A'))
    pdf.setFont('Helvetica-Bold', 9)
    pdf.drawString(margin + 8, y - 11, "CONTROL DE LA SESIÓN (ARQUEO DE CAJA)")
    y -= 22

    pdf.setFillColor(colors.HexColor('#64748B'))
    pdf.setFont('Helvetica-Bold', 8)
    pdf.drawString(margin + 8, y, "Nombre")
    pdf.drawRightString(width - margin - 170, y, "Esperado")
    pdf.drawRightString(width - margin - 90, y, "Contado")
    pdf.drawRightString(width - margin - 8, y, "Diferencia")
    y -= 14

    metodos_arqueo = [
        ('Efectivo CAFETO 24', 'efectivo_esperado', 'efectivo_real', 'diferencia'),
        ('Nequi CAFETO 24', 'nequi_esperado', 'nequi_real', 'diferencia_nequi'),
        ('Daviplata CAFETO 24', 'daviplata_esperado', 'daviplata_real', 'diferencia_daviplata'),
        ('Tarjeta CAFETO 24', 'tarjeta_esperado', 'tarjeta_real', 'diferencia_tarjeta')
    ]

    for nombre_m, esp_k, real_k, dif_k in metodos_arqueo:
        if turno.get(real_k) is not None or turno.get(esp_k) is not None:
            check_page(16)
            esp = float(turno.get(esp_k) or 0)
            real = float(turno.get(real_k) or 0)
            dif = float(turno.get(dif_k) or 0)
            
            pdf.setFillColor(colors.HexColor('#1E293B'))
            pdf.setFont('Helvetica-Bold', 8)
            pdf.drawString(margin + 8, y, nombre_m)
            pdf.setFont('Helvetica', 8)
            pdf.drawRightString(width - margin - 170, y, f"${esp:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
            pdf.drawRightString(width - margin - 90, y, f"${real:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
            
            if dif != 0:
                pdf.setFillColor(colors.HexColor('#DC2626') if dif < 0 else colors.HexColor('#2563EB'))
            else:
                pdf.setFillColor(colors.HexColor('#16A34A'))
            pdf.drawRightString(width - margin - 8, y, f"${dif:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
            y -= 14

    y -= 10

    # 5. OBSERVACIONES Y NOTAS DEL CIERRE
    obs = str(turno.get('observaciones') or '').strip()
    if obs:
        check_page(35)
        pdf.setFillColor(colors.HexColor('#FEF3C7'))
        pdf.rect(margin, y - 24, usable_width, 24, fill=1, stroke=1)
        pdf.setStrokeColor(colors.HexColor('#F59E0B'))
        pdf.setFillColor(colors.HexColor('#92400E'))
        pdf.setFont('Helvetica-Bold', 8)
        pdf.drawString(margin + 8, y - 10, "OBSERVACIONES / NOTAS DE CIERRE DE TURNO:")
        pdf.setFont('Helvetica', 8)
        pdf.drawString(margin + 8, y - 20, obs[:110])
        y -= 32

    # 6. GASTOS DETALLADOS DEL TURNO
    if gastos_turno:
        check_page(40)
        pdf.setFillColor(colors.HexColor('#E2E8F0'))
        pdf.rect(margin, y - 14, usable_width, 16, fill=1, stroke=0)
        pdf.setFillColor(colors.HexColor('#0F172A'))
        pdf.setFont('Helvetica-Bold', 9)
        pdf.drawString(margin + 8, y - 11, f"GASTOS REGISTRADOS EN TURNO ({len(gastos_turno)})")
        y -= 22

        pdf.setFillColor(colors.HexColor('#64748B'))
        pdf.setFont('Helvetica-Bold', 8)
        pdf.drawString(margin + 8, y, "Fecha / Hora")
        pdf.drawString(margin + 110, y, "Método")
        pdf.drawString(margin + 160, y, "Tipo / Producto / Insumo / Descripción")
        pdf.drawRightString(width - margin - 8, y, "Monto")
        y -= 14

        tot_g_pdf = 0
        for g in gastos_turno:
            check_page(16)
            f_g = str(g.get('fecha') or '')
            met_g = str(g.get('metodo_pago') or 'efectivo').upper()
            cat_g = str(g.get('categoria_gasto') or 'General').strip()
            desc_g = str(g.get('descripcion') or '').strip()
            detalle_completo = f"[{cat_g}] {desc_g}"
            m_gonto = float(g.get('monto') or 0)
            tot_g_pdf += m_gonto

            pdf.setFillColor(colors.HexColor('#334155'))
            pdf.setFont('Helvetica', 8)
            pdf.drawString(margin + 8, y, f_g)
            pdf.drawString(margin + 110, y, met_g)
            pdf.drawString(margin + 160, y, detalle_completo[:55])
            
            pdf.setFillColor(colors.HexColor('#DC2626'))
            pdf.drawRightString(width - margin - 8, y, f"-${m_gonto:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
            y -= 14

        check_page(20)
        pdf.setFillColor(colors.HexColor('#1E293B'))
        pdf.setFont('Helvetica-Bold', 8)
        pdf.drawString(margin + 8, y, "Total Gastos Registrados")
        pdf.drawRightString(width - margin - 8, y, f"-${tot_g_pdf:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
        y -= 18

    # 7. MOVIMIENTOS DE CAJA REGISTRADOS
    if movimientos_turno:
        check_page(40)
        pdf.setFillColor(colors.HexColor('#E2E8F0'))
        pdf.rect(margin, y - 14, usable_width, 16, fill=1, stroke=0)
        pdf.setFillColor(colors.HexColor('#0F172A'))
        pdf.setFont('Helvetica-Bold', 9)
        pdf.drawString(margin + 8, y - 11, f"MOVIMIENTOS DE CAJA REGISTRADOS EN TURNO ({len(movimientos_turno)})")
        y -= 22

        pdf.setFillColor(colors.HexColor('#64748B'))
        pdf.setFont('Helvetica-Bold', 8)
        pdf.drawString(margin + 8, y, "Fecha / Hora")
        pdf.drawString(margin + 110, y, "Método")
        pdf.drawString(margin + 170, y, "Descripción / Origen")
        pdf.drawRightString(width - margin - 8, y, "Monto")
        y -= 14

        for mov in movimientos_turno:
            check_page(16)
            f_mov = str(mov.get('fecha') or '')
            met_mov = str(mov.get('metodo_pago') or 'efectivo').upper()
            desc_mov = f"{mov.get('origen', '')}: {mov.get('descripcion', '')}".strip(': ')
            tipo_mov = str(mov.get('tipo_movimiento') or '').lower()
            signo = '+' if tipo_mov in ('entrada', 'deuda', 'abono') else '-'
            m_monto = float(mov.get('monto') or 0)

            pdf.setFillColor(colors.HexColor('#334155'))
            pdf.setFont('Helvetica', 8)
            pdf.drawString(margin + 8, y, f_mov)
            pdf.drawString(margin + 110, y, met_mov)
            pdf.drawString(margin + 170, y, desc_mov[:45])
            
            pdf.setFillColor(colors.HexColor('#DC2626') if signo == '-' else colors.HexColor('#16A34A'))
            pdf.drawRightString(width - margin - 8, y, f"{signo}${m_monto:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
            y -= 14
        y -= 10

    pdf.save()
    return ruta_pdf
def api_descargar_pdf_turno(turno_id):
    if 'usuario' not in session:
        return redirect(url_for('login'))

    try:
        archivos = generar_reportes_cierre_turno(turno_id)
        if not archivos or not archivos.get('pdf'):
            return jsonify({'error': 'No se pudo generar el reporte PDF del turno', 'success': False}), 500

        directorio = _obtener_directorio_reportes_cierre()
        return send_from_directory(
            directorio, 
            archivos['pdf'], 
            as_attachment=True, 
            download_name=f"Reporte_Turno_{turno_id}.pdf"
        )
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500

def generar_pdf_reporte_seccion_z(titulo_reporte, subtexto_id, fecha_inicio, fecha_fin, secciones_datos):
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    reportes_dir = _obtener_directorio_reportes_cierre()
    nombre_archivo = f"reporte_{titulo_reporte.lower().replace(' ', '_')}_{timestamp}.pdf"
    ruta_pdf = os.path.join(reportes_dir, nombre_archivo)

    pdf = canvas.Canvas(ruta_pdf, pagesize=letter)
    width, height = letter
    margin = 36
    usable_width = width - (margin * 2)
    y = height - margin

    def check_page(needed=30):
        nonlocal y
        if y - needed < margin:
            pdf.showPage()
            y = height - margin

    # TOP HEADER BANNER (Estilo Reporte Z de Detalles de ventas.pdf)
    pdf.setFillColor(colors.HexColor('#F1F5F9'))
    pdf.rect(margin, y - 70, usable_width, 70, fill=1, stroke=0)
    
    pdf.setFillColor(colors.HexColor('#1E293B'))
    pdf.setFont('Helvetica-Bold', 12)
    pdf.drawString(margin + 12, y - 18, "CAFETO 24")
    pdf.setFont('Helvetica', 8)
    pdf.setFillColor(colors.HexColor('#64748B'))
    pdf.drawString(margin + 12, y - 30, "Diagonal 62 sur #22-04")
    pdf.drawString(margin + 12, y - 40, "Bogotá D.C., Colombia")
    pdf.drawString(margin + 12, y - 50, "NIT / IVA: 1013587664-8")

    pdf.setFillColor(colors.HexColor('#0F172A'))
    pdf.setFont('Helvetica-Bold', 14)
    pdf.drawRightString(width - margin - 12, y - 22, str(titulo_reporte))
    pdf.setFont('Helvetica', 8)
    pdf.setFillColor(colors.HexColor('#475569'))
    pdf.drawRightString(width - margin - 12, y - 38, f"Consulta: {subtexto_id}")

    y -= 80

    pdf.setFillColor(colors.HexColor('#0F172A'))
    pdf.setFont('Helvetica', 8)
    date_str = f"Rango: {fecha_inicio} a {fecha_fin}" if (fecha_inicio or fecha_fin) else f"Emisión: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    pdf.drawCentredString(width / 2, y, date_str)
    y -= 16

    for sec in secciones_datos:
        check_page(40)
        sec_title = sec.get('titulo', '')
        if sec_title:
            pdf.setFillColor(colors.HexColor('#E2E8F0'))
            pdf.rect(margin, y - 14, usable_width, 16, fill=1, stroke=0)
            pdf.setFillColor(colors.HexColor('#0F172A'))
            pdf.setFont('Helvetica-Bold', 9)
            pdf.drawString(margin + 8, y - 11, sec_title.upper())
            y -= 20

        tipo = sec.get('tipo', 'tabla')
        
        if tipo == 'kv':
            for k, v in sec.get('datos', {}).items():
                check_page(16)
                pdf.setFillColor(colors.HexColor('#1E293B'))
                pdf.setFont('Helvetica-Bold', 8)
                pdf.drawString(margin + 8, y, str(k))
                pdf.setFont('Helvetica', 8)
                pdf.drawRightString(width - margin - 8, y, str(v))
                y -= 14
        elif tipo == 'categorias':
            for cat in sec.get('categorias', []):
                check_page(24)
                pdf.setFillColor(colors.HexColor('#1E293B'))
                pdf.setFont('Helvetica-Bold', 9)
                pdf.drawString(margin + 8, y, str(cat['nombre']))
                cant_str = f"{float(cat.get('cantidad', 0)):.1f}"
                tot_str = f"${float(cat.get('total', 0)):,.0f}".replace(',', '.')
                pdf.drawRightString(width - margin - 120, y, cant_str)
                pdf.drawRightString(width - margin - 8, y, tot_str)
                y -= 4
                pdf.setStrokeColor(colors.HexColor('#CBD5E1'))
                pdf.setLineWidth(0.5)
                pdf.line(margin + 8, y, width - margin - 8, y)
                y -= 12

                for p in cat.get('items', []):
                    check_page(16)
                    pdf.setFillColor(colors.HexColor('#475569'))
                    pdf.setFont('Helvetica', 8)
                    pdf.drawString(margin + 24, y, str(p['nombre']))
                    p_cant = f"{float(p.get('cantidad', 0)):.1f} Unidades"
                    p_tot = f"${float(p.get('total', 0)):,.0f}".replace(',', '.')
                    pdf.drawRightString(width - margin - 120, y, p_cant)
                    pdf.drawRightString(width - margin - 8, y, p_tot)
                    y -= 14
                y -= 4
        elif tipo == 'tabla':
            headers = sec.get('headers', [])
            filas = sec.get('filas', [])
            
            # Calcular anchos y posiciones de columnas proporcionales
            col_positions = []
            col_widths = []
            if headers:
                num_cols = len(headers)
                if num_cols == 7:
                    # Columnas del reporte de turnos: Turno, Cajero, Apertura, Cierre, Estado, Efectivo, Diferencia
                    col_pcts = [0.12, 0.16, 0.22, 0.22, 0.10, 0.10, 0.08]
                elif num_cols == 5:
                    # Reporte de transacciones: Fecha, Método, Vendedor, Referencia, Monto
                    col_pcts = [0.24, 0.12, 0.24, 0.25, 0.15]
                elif num_cols == 4:
                    # Reporte de Saldos
                    col_pcts = [0.34, 0.22, 0.22, 0.22]
                else:
                    col_pcts = [1.0 / num_cols] * num_cols
                
                current_x = margin
                for pct in col_pcts:
                    col_positions.append(current_x)
                    col_widths.append(pct * usable_width)
                    current_x += pct * usable_width
            
            if headers:
                check_page(20)
                pdf.setFillColor(colors.HexColor('#F8FAFC'))
                pdf.rect(margin, y - 12, usable_width, 14, fill=1, stroke=0)
                pdf.setFillColor(colors.HexColor('#475569'))
                pdf.setFont('Helvetica-Bold', 8)
                for idx_h, h in enumerate(headers):
                    x_pos = col_positions[idx_h]
                    if idx_h == len(headers) - 1:
                        pdf.drawRightString(x_pos + col_widths[idx_h] - 6, y - 10, str(h))
                    else:
                        pdf.drawString(x_pos + 6, y - 10, str(h))
                y -= 22  # Espaciado vertical incrementado para evitar superposición con la primera fila

            for f in filas:
                check_page(16)
                pdf.setFillColor(colors.HexColor('#334155'))
                pdf.setFont('Helvetica', 8)
                if isinstance(f, (list, tuple)):
                    for idx_c, cell in enumerate(f):
                        x_pos = col_positions[idx_c]
                        align_right = (idx_c == len(f) - 1 and ('$' in str(cell) or str(cell).replace('.','').replace(',','').isdigit()))
                        if align_right:
                            pdf.drawRightString(x_pos + col_widths[idx_c] - 6, y, str(cell))
                        else:
                            pdf.drawString(x_pos + 6, y, str(cell))
                else:
                    pdf.drawString(margin + 6, y, str(f))
                y -= 4
                pdf.setStrokeColor(colors.HexColor('#F1F5F9'))
                pdf.setLineWidth(0.4)
                pdf.line(margin + 4, y, width - margin - 4, y)
                y -= 10

        y -= 6

    total_val = sec.get('total_general')
    if total_val is not None:
        check_page(30)
        pdf.setFillColor(colors.HexColor('#F1F5F9'))
        pdf.rect(margin, y - 20, usable_width, 22, fill=1, stroke=1)
        pdf.setStrokeColor(colors.HexColor('#CBD5E1'))
        pdf.setFillColor(colors.HexColor('#0F172A'))
        pdf.setFont('Helvetica-Bold', 10)
        pdf.drawString(margin + 12, y - 14, "TOTAL GENERAL")
        pdf.drawRightString(width - margin - 12, y - 14, f"${float(total_val):,.0f}".replace(',', '.'))

    pdf.save()
    return nombre_archivo

@app.route('/api/informes/pdf/<seccion>')
def api_descargar_pdf_informe_seccion(seccion):
    if 'usuario' not in session:
        return redirect(url_for('login'))

    fecha_inicio = request.args.get('fecha_inicio', '')
    fecha_fin = request.args.get('fecha_fin', '')
    
    db = conectar_db()
    try:
        secciones_datos = []
        titulo_pdf = "Reporte de Informes"
        subtexto_id = f"Sección: {seccion.upper()}"

        if seccion == 'resumen_general':
            titulo_pdf = "Reporte Z - Resumen General"
            gen = db.execute("""
                SELECT
                    (SELECT IFNULL(SUM(total), 0) FROM ventas) as venta_total,
                    (SELECT IFNULL(SUM(monto), 0) FROM gastos) as total_gastos,
                    (SELECT IFNULL(SUM(monto), 0) FROM abonos_deuda) as total_abonos,
                    (SELECT IFNULL(SUM(saldo_pendiente), 0) FROM deudas_clientes WHERE estado = 'pendiente') as total_fiado
            """).fetchone()
            
            secciones_datos.append({
                'titulo': 'Métricas Generales del Negocio',
                'tipo': 'kv',
                'datos': {
                    'Ventas Totales Brutas': f"${float(gen['venta_total'] or 0):,.0f}".replace(',', '.'),
                    'Gastos Totales Registrados': f"${float(gen['total_gastos'] or 0):,.0f}".replace(',', '.'),
                    'Abonos Recibidos': f"${float(gen['total_abonos'] or 0):,.0f}".replace(',', '.'),
                    'Deuda Fiada Pendiente': f"${float(gen['total_fiado'] or 0):,.0f}".replace(',', '.'),
                    'Balance Neto Estimado': f"${float(gen['venta_total'] - gen['total_gastos']):,.0f}".replace(',', '.')
                }
            })

        elif seccion == 'productos_vendidos':
            titulo_pdf = "Reporte Z - Productos Vendidos"
            cats = db.execute("""
                SELECT 
                    c.nombre_categoria as categoria,
                    p.nombre as producto,
                    SUM(dv.cantidad) as cantidad,
                    SUM(dv.cantidad * dv.precio_unitario) as total
                FROM detalle_ventas dv
                JOIN productos p ON dv.producto_id = p.id
                JOIN categorias c ON p.categoria_id = c.id
                GROUP BY c.id, p.id
                ORDER BY c.nombre_categoria ASC, total DESC
            """).fetchall()

            categorias_map = {}
            for row in cats:
                cat_name = row['categoria']
                if cat_name not in categorias_map:
                    categorias_map[cat_name] = {'nombre': cat_name, 'cantidad': 0, 'total': 0, 'items': []}
                categorias_map[cat_name]['cantidad'] += float(row['cantidad'] or 0)
                categorias_map[cat_name]['total'] += float(row['total'] or 0)
                categorias_map[cat_name]['items'].append({
                    'nombre': row['producto'],
                    'cantidad': float(row['cantidad'] or 0),
                    'total': float(row['total'] or 0)
                })

            secciones_datos.append({
                'titulo': 'Ventas por Categoría de Productos',
                'tipo': 'categorias',
                'categorias': list(categorias_map.values())
            })

        elif seccion == 'metodos_pago':
            titulo_pdf = "Reporte Z - Saldo por Métodos de Pago"
            movs = db.execute("""
                WITH entradas AS (
                    SELECT COALESCE(metodo_pago, 'efectivo') as metodo, SUM(total) as monto FROM ventas GROUP BY metodo
                    UNION ALL
                    SELECT COALESCE(metodo_pago, 'efectivo') as metodo, SUM(monto) as monto FROM abonos_deuda GROUP BY metodo
                ),
                salidas AS (
                    SELECT COALESCE(metodo_pago, 'efectivo') as metodo, SUM(monto) as monto FROM gastos GROUP BY metodo
                )
                SELECT 
                    e.metodo,
                    SUM(e.monto) as entradas,
                    COALESCE((SELECT SUM(monto) FROM salidas WHERE metodo = e.metodo), 0) as salidas,
                    (SUM(e.monto) - COALESCE((SELECT SUM(monto) FROM salidas WHERE metodo = e.metodo), 0)) as saldo
                FROM entradas e
                GROUP BY e.metodo
            """).fetchall()

            filas = []
            for m in movs:
                filas.append([
                    str(m['metodo']).upper(),
                    f"${float(m['entradas'] or 0):,.0f}".replace(',', '.'),
                    f"${float(m['salidas'] or 0):,.0f}".replace(',', '.'),
                    f"${float(m['saldo'] or 0):,.0f}".replace(',', '.')
                ])

            secciones_datos.append({
                'titulo': 'Control de Cuentas y Saldos',
                'tipo': 'tabla',
                'headers': ['Método de Pago', 'Entradas', 'Salidas', 'Saldo Actual'],
                'filas': filas
            })

        elif seccion == 'pagos_digitales':
            titulo_pdf = "Reporte Z - Auditoría de Pagos Digitales"
            digitales = db.execute("""
                SELECT v.fecha, COALESCE(v.metodo_pago, 'efectivo') as metodo, u.nombre as vendedor, v.total, COALESCE(v.referencia_pago, '') as ref
                FROM ventas v
                JOIN usuarios u ON v.vendedor_id = u.id
                WHERE COALESCE(v.metodo_pago, 'efectivo') IN ('nequi', 'daviplata', 'tarjeta')
                ORDER BY v.id DESC
            """).fetchall()

            filas = []
            tot_dig = 0
            for d in digitales:
                t = float(d['total'] or 0)
                tot_dig += t
                filas.append([
                    str(d['fecha'] or ''),
                    str(d['metodo'] or '').upper(),
                    str(d['vendedor'] or ''),
                    str(d['ref'] or '-'),
                    f"${t:,.0f}".replace(',', '.')
                ])

            secciones_datos.append({
                'titulo': 'Transacciones Nequi, Daviplata y Tarjeta',
                'tipo': 'tabla',
                'headers': ['Fecha', 'Método', 'Vendedor', 'Referencia', 'Monto'],
                'filas': filas,
                'total_general': tot_dig
            })

        elif seccion == 'gastos_detallados':
            titulo_pdf = "Reporte Z - Detalle de Gastos"
            gastos = db.execute("""
                SELECT g.fecha, u.nombre as responsable, g.metodo_pago, g.categoria_gasto, g.descripcion, g.monto
                FROM gastos g
                JOIN usuarios u ON g.vendedor_id = u.id
                ORDER BY g.id DESC
            """).fetchall()

            filas = []
            tot_g = 0
            for g in gastos:
                m = float(g['monto'] or 0)
                tot_g += m
                filas.append([
                    str(g['fecha'] or ''),
                    str(g['responsable'] or ''),
                    str(g['metodo_pago'] or '').upper(),
                    str(g['categoria_gasto'] or ''),
                    str(g['descripcion'] or ''),
                    f"${m:,.0f}".replace(',', '.')
                ])

            secciones_datos.append({
                'titulo': 'Historial Completo de Salidas de Caja',
                'tipo': 'tabla',
                'headers': ['Fecha', 'Responsable', 'Método', 'Categoría', 'Descripción', 'Monto'],
                'filas': filas,
                'total_general': tot_g
            })

        elif seccion == 'deudas_fiadas':
            titulo_pdf = "Reporte Z - Cuentas Fiadas y Deudas"
            deudas = db.execute("""
                SELECT cliente_nombre, COUNT(id) as ops, SUM(monto_total) as total, SUM(monto_pagado) as pagado, SUM(saldo_pendiente) as saldo
                FROM deudas_clientes
                WHERE estado = 'pendiente'
                GROUP BY cliente_nombre
                ORDER BY saldo DESC
            """).fetchall()

            filas = []
            tot_s = 0
            for d in deudas:
                s = float(d['saldo'] or 0)
                tot_s += s
                filas.append([
                    str(d['cliente_nombre']),
                    f"{d['ops']} compras",
                    f"${float(d['total'] or 0):,.0f}".replace(',', '.'),
                    f"${float(d['pagado'] or 0):,.0f}".replace(',', '.'),
                    f"${s:,.0f}".replace(',', '.')
                ])

            secciones_datos.append({
                'titulo': 'Deudores con Saldo Pendiente de Cobro',
                'tipo': 'tabla',
                'headers': ['Cliente / Deudor', 'Operaciones', 'Total Fiado', 'Total Pagado', 'Saldo Pendiente'],
                'filas': filas,
                'total_general': tot_s
            })

        elif seccion == 'rendimiento_cajeros':
            titulo_pdf = "Reporte Z - Rendimiento Técnico de Cajeros"
            cajeros = db.execute("""
                SELECT 
                    COALESCE(u.nombre, 'Cajero General') as cajero,
                    COALESCE(u.rol, 'cajero') as rol,
                    COUNT(v.id) as num_ventas,
                    SUM(v.total) as total_vendido
                FROM ventas v
                LEFT JOIN usuarios u ON v.vendedor_id = u.id
                GROUP BY v.vendedor_id
                ORDER BY total_vendido DESC
            """).fetchall()

            filas = []
            tot_rend = 0
            for c in cajeros:
                t = float(c['total_vendido'] or 0)
                tot_rend += t
                filas.append([
                    str(c['cajero']),
                    str(c['rol']).upper(),
                    f"{c['num_ventas']} ventas",
                    f"${t:,.0f}".replace(',', '.')
                ])

            secciones_datos.append({
                'titulo': 'Desglose de Recaudo por Colaborador / Cajero',
                'tipo': 'tabla',
                'headers': ['Cajero / Usuario', 'Rol', 'Ventas Realizadas', 'Total Recaudado'],
                'filas': filas,
                'total_general': tot_rend
            })

        elif seccion == 'saldos_cuentas':
            titulo_pdf = "Reporte Z - Saldos por Cuenta y Método de Pago"
            saldos = db.execute("""
                SELECT
                    m.metodo,
                    (SELECT IFNULL(SUM(total), 0) FROM ventas WHERE COALESCE(metodo_pago, 'efectivo') = m.metodo) as entradas,
                    (SELECT IFNULL(SUM(monto), 0) FROM gastos WHERE COALESCE(metodo_pago, 'efectivo') = m.metodo) as salidas,
                    ((SELECT IFNULL(SUM(total), 0) FROM ventas WHERE COALESCE(metodo_pago, 'efectivo') = m.metodo) - 
                     (SELECT IFNULL(SUM(monto), 0) FROM gastos WHERE COALESCE(metodo_pago, 'efectivo') = m.metodo)) as saldo
                FROM (
                    SELECT 'efectivo' as metodo UNION SELECT 'nequi' UNION SELECT 'daviplata' UNION SELECT 'tarjeta'
                ) m
            """).fetchall()

            filas = []
            for s in saldos:
                filas.append([
                    str(s['metodo']).upper(),
                    f"${float(s['entradas'] or 0):,.0f}".replace(',', '.'),
                    f"${float(s['salidas'] or 0):,.0f}".replace(',', '.'),
                    f"${float(s['saldo'] or 0):,.0f}".replace(',', '.')
                ])

            secciones_datos.append({
                'titulo': 'Resumen Consolidado de Saldos por Cuenta',
                'tipo': 'tabla',
                'headers': ['Método de Pago', 'Entradas', 'Salidas', 'Saldo Actual'],
                'filas': filas
            })

        elif seccion == 'turnos':
            titulo_pdf = "Reporte Z - Historial de Turnos de Caja"
            turnos_list = db.execute("""
                SELECT 
                    t.id,
                    t.fecha_apertura,
                    t.fecha_cierre,
                    t.estado,
                    COALESCE(t.nombre_usuario, u.nombre, 'Cajero') as cajero,
                    COALESCE(t.efectivo_real, 0) as efectivo_real,
                    COALESCE(t.diferencia, 0) as diferencia
                FROM turnos t
                LEFT JOIN usuarios u ON u.id = t.usuario_id
                ORDER BY t.id DESC
            """).fetchall()

            filas = []
            for t in turnos_list:
                est = 'ABIERTO' if t['estado'] == 'abierto' else 'CERRADO'
                filas.append([
                    f"Turno #{int(t['id']):04d}",
                    str(t['cajero']),
                    str(t['fecha_apertura'] or ''),
                    str(t['fecha_cierre'] or 'En curso'),
                    est,
                    f"${float(t['efectivo_real'] or 0):,.0f}".replace(',', '.'),
                    f"${float(t['diferencia'] or 0):,.0f}".replace(',', '.')
                ])

            secciones_datos.append({
                'titulo': 'Registro Completo de Turnos Aperturados y Cerrados',
                'tipo': 'tabla',
                'headers': ['Turno', 'Cajero', 'Apertura', 'Cierre', 'Estado', 'Efectivo Cierre', 'Diferencia'],
                'filas': filas
            })

        else:
            titulo_pdf = f"Reporte Z - {seccion.replace('_', ' ').title()}"
            secciones_datos.append({
                'titulo': 'Información Consolidada',
                'tipo': 'kv',
                'datos': {'Fecha Consulta': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            })

        nombre_pdf = generar_pdf_reporte_seccion_z(titulo_pdf, subtexto_id, fecha_inicio, fecha_fin, secciones_datos)
        directorio = _obtener_directorio_reportes_cierre()
        return send_from_directory(directorio, nombre_pdf, as_attachment=True, download_name=f"{nombre_pdf}")
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500
    finally:
        db.close()

# ==========================================
# 1. RUTA PRINCIPAL Y LOGOUT
# ==========================================
@app.route('/')
def inicio():
    if 'usuario' in session:
        if session['rol'] == 'administrador':
            return redirect(url_for('panel_administrador'))
        return redirect(url_for('pantalla_ventas'))
    return redirect(url_for('login'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ==========================================
# 2. PROCESAR LOGIN
# ==========================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        usuario_ingresado = request.form['username']
        password_ingresada = request.form['password']
        
        db = conectar_db()
        usuario = db.execute(
            "SELECT * FROM usuarios WHERE usuario = ? AND contrasena = ?", 
            (usuario_ingresado, password_ingresada)
        ).fetchone()
        db.close()
        
        if usuario:
            session['id'] = usuario['id']
            session['usuario'] = usuario['usuario']
            session['nombre'] = usuario['nombre']
            session['rol'] = usuario['rol']
            session['turno_id'] = obtener_turno_activo(usuario['id'])
            
            if usuario['rol'] == 'administrador':
                return redirect(url_for('panel_administrador'))
            return redirect(url_for('pantalla_ventas'))
        else:
            return render_template('login.html', error="Usuario o contraseña incorrectos.")
            
    return render_template('login.html')


@app.route('/turno/iniciar', methods=['POST'])
def iniciar_turno():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    usuario = {
        'id': session.get('id'),
        'nombre': session.get('nombre'),
        'rol': session.get('rol')
    }
    turno_id = abrir_turno(usuario)
    session['turno_id'] = turno_id
    flash('✅ Turno iniciado correctamente.', 'success')
    return redirect(request.referrer or url_for('pantalla_ventas'))


@app.route('/api/turno/preliminar')
def api_turno_preliminar():
    if 'usuario' not in session:
        return jsonify({'error': 'No autorizado', 'success': False}), 401

    usuario_id = session.get('id')
    turno_id = session.get('turno_id') or obtener_turno_activo(usuario_id)
    if not turno_id:
        return jsonify({'success': False, 'error': 'No hay turno activo.'}), 400

    db = conectar_db()
    try:
        turno = db.execute("""
            SELECT id, nombre_usuario, rol, fecha_apertura
            FROM turnos
            WHERE id = ?
        """, (turno_id,)).fetchone()

        if not turno:
            return jsonify({'success': False, 'error': 'No se encontró el turno.'}), 404

        # Resumen de este turno únicamente (ventas/gastos del turno)
        resumen_turno = db.execute("""
            SELECT
                COALESCE(SUM(total), 0) as total_ventas,
                COUNT(id) as cantidad_ventas
            FROM ventas
            WHERE turno_id = ?
        """, (turno_id,)).fetchone()

        gastos_turno = db.execute("""
            SELECT COALESCE(SUM(monto), 0) as total_gastos
            FROM gastos
            WHERE turno_id = ?
        """, (turno_id,)).fetchone()

        # Saldos acumulados históricos (de toda la base de datos, fuera del turno)
        saldos_acumulados_db = db.execute("""
            WITH movimientos AS (
                SELECT COALESCE(metodo_pago, 'efectivo') as metodo_pago, SUM(total) as entradas, 0 as salidas
                FROM ventas
                WHERE COALESCE(metodo_pago, 'efectivo') != 'fiado'
                GROUP BY COALESCE(metodo_pago, 'efectivo')
                UNION ALL
                SELECT COALESCE(metodo_pago, 'efectivo') as metodo_pago, SUM(monto) as entradas, 0 as salidas
                FROM abonos_deuda
                GROUP BY COALESCE(metodo_pago, 'efectivo')
                UNION ALL
                SELECT COALESCE(metodo_pago, 'efectivo') as metodo_pago, 0 as entradas, SUM(monto) as salidas
                FROM gastos
                GROUP BY COALESCE(metodo_pago, 'efectivo')
            )
            SELECT
                metodo_pago,
                (SUM(entradas) - SUM(salidas)) as saldo
            FROM movimientos
            GROUP BY metodo_pago
        """).fetchall()

        metodos = ['efectivo', 'nequi', 'daviplata', 'tarjeta']
        saldos_acumulados = {m: 0.0 for m in metodos}
        for fila in saldos_acumulados_db:
            metodo = str(fila['metodo_pago']).lower()
            saldos_acumulados[metodo] = float(fila['saldo'] or 0)

        efectivo_esperado = saldos_acumulados['efectivo']

        return jsonify({
            'success': True,
            'turno': {
                'id': int(turno['id']),
                'usuario': turno['nombre_usuario'],
                'rol': turno['rol'],
                'fecha_apertura': turno['fecha_apertura']
            },
            'turno_ventas': float(resumen_turno['total_ventas'] or 0),
            'turno_gastos': float(gastos_turno['total_gastos'] or 0),
            'cantidad_ventas': int(resumen_turno['cantidad_ventas'] or 0),
            'saldos_acumulados': saldos_acumulados,
            'efectivo_esperado': efectivo_esperado
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        db.close()


@app.route('/api/turno/<int:turno_id>/pdf')
def api_descargar_pdf_turno_individual(turno_id):
    if 'usuario' not in session:
        return redirect(url_for('login'))

    try:
        resultado = generar_archivos_cierre_turno(turno_id)
        if not resultado or not resultado.get('pdf'):
            return "No se pudo generar el reporte PDF para este turno", 500

        directorio = _obtener_directorio_reportes_cierre()
        return send_from_directory(directorio, resultado['pdf'], as_attachment=False)
    except Exception as e:
        print(f"Error generando PDF del turno {turno_id}: {e}")
        return f"Error al generar PDF del turno: {str(e)}", 500

@app.route('/api/turno/<int:turno_id>/detalle')
def api_turno_detalle(turno_id):
    if 'usuario' not in session:
        return jsonify({'success': False, 'error': 'No autorizado'}), 401

    db = conectar_db()
    try:
        productos_vendidos = db.execute("""
            SELECT 
                p.nombre,
                SUM(vi.cantidad) as cantidad_total,
                SUM(vi.cantidad * vi.precio_unitario) as monto_total
            FROM detalle_ventas vi
            JOIN ventas v ON vi.venta_id = v.id
            JOIN productos p ON vi.producto_id = p.id
            WHERE v.turno_id = ?
            GROUP BY p.id, p.nombre
            ORDER BY monto_total DESC
        """, (turno_id,)).fetchall()

        gastos = db.execute("""
            SELECT 'gasto' as tipo, COALESCE(descripcion, 'Gasto sin descripción') as descripcion, COALESCE(metodo_pago, 'efectivo') as metodo_pago, monto
            FROM gastos
            WHERE turno_id = ?
        """, (turno_id,)).fetchall()

        abonos = db.execute("""
            SELECT 'abono' as tipo, ('Abono cliente: ' || COALESCE(cliente_nombre, 'Genérico')) as descripcion, COALESCE(metodo_pago, 'efectivo') as metodo_pago, monto
            FROM abonos_deuda
            WHERE turno_id = ?
        """, (turno_id,)).fetchall()

        movimientos_manuales = db.execute("""
            SELECT tipo_movimiento as tipo, descripcion, COALESCE(metodo_pago, 'efectivo') as metodo_pago, monto
            FROM caja_movimientos
            WHERE turno_id = ?
        """, (turno_id,)).fetchall()

        movimientos = []
        for g in gastos:
            movimientos.append({
                'tipo': 'Gasto 💸',
                'descripcion': g['descripcion'],
                'metodo_pago': g['metodo_pago'],
                'monto': float(g['monto'] or 0)
            })
        for a in abonos:
            movimientos.append({
                'tipo': 'Abono Deuda 📥',
                'descripcion': a['descripcion'],
                'metodo_pago': a['metodo_pago'],
                'monto': float(a['monto'] or 0)
            })
        for m in movimientos_manuales:
            movimientos.append({
                'tipo': f"Mov: {m['tipo']}",
                'descripcion': m['descripcion'],
                'metodo_pago': m['metodo_pago'],
                'monto': float(m['monto'] or 0)
            })

        prod_list = []
        for p in productos_vendidos:
            prod_list.append({
                'nombre': p['nombre'],
                'cantidad': int(p['cantidad_total'] or 0),
                'total': float(p['monto_total'] or 0)
            })

        return jsonify({
            'success': True,
            'productos': prod_list,
            'movimientos': movimientos
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        db.close()


@app.route('/api/ordenes_abiertas')
def api_obtener_ordenes_abiertas():
    if 'usuario' not in session:
        return jsonify({'success': False, 'error': 'No autorizado'}), 401

    db = conectar_db()
    try:
        ordenes = db.execute("""
            SELECT id, mesa_cliente, fecha_creacion, estado, observaciones, total
            FROM ordenes_abiertas
            WHERE estado = 'abierta'
            ORDER BY id DESC
        """).fetchall()

        resultado = []
        for o in ordenes:
            items = db.execute("""
                SELECT producto_id as id, nombre_producto as nombre, precio_unitario as precio, cantidad, subtotal
                FROM ordenes_abiertas_items
                WHERE orden_id = ?
            """, (o['id'],)).fetchall()

            resultado.append({
                'id': o['id'],
                'mesa_cliente': o['mesa_cliente'],
                'fecha_creacion': o['fecha_creacion'],
                'observaciones': o['observaciones'] or '',
                'total': float(o['total'] or 0),
                'items': [dict(item) for item in items]
            })

        return jsonify({'success': True, 'ordenes': resultado})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        db.close()


@app.route('/api/ordenes_abiertas/guardar', methods=['POST'])
def api_guardar_orden_abierta():
    if 'usuario' not in session:
        return jsonify({'success': False, 'error': 'No autorizado'}), 401

    data = request.get_json() or {}
    orden_id = data.get('orden_id')
    mesa_cliente = (data.get('mesa_cliente') or '').strip()
    items = data.get('items', [])

    if not mesa_cliente:
        return jsonify({'success': False, 'error': 'Debes especificar el nombre de la mesa o cliente.'}), 400

    usuario_id = session.get('id')
    turno_id = session.get('turno_id') or obtener_turno_activo(usuario_id)

    db = conectar_db()
    try:
        total_orden = sum(float(item.get('precio', 0)) * int(item.get('cantidad', 1)) for item in items)
        fecha_ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if orden_id:
            db.execute("""
                UPDATE ordenes_abiertas
                SET mesa_cliente = ?, total = ?, observaciones = ?
                WHERE id = ? AND estado = 'abierta'
            """, (mesa_cliente, total_orden, data.get('observaciones', ''), orden_id))
            db.execute("DELETE FROM ordenes_abiertas_items WHERE orden_id = ?", (orden_id,))
        else:
            cursor = db.execute("""
                INSERT INTO ordenes_abiertas (mesa_cliente, vendedor_id, turno_id, fecha_creacion, estado, observaciones, total)
                VALUES (?, ?, ?, ?, 'abierta', ?, ?)
            """, (mesa_cliente, usuario_id, turno_id, fecha_ahora, data.get('observaciones', ''), total_orden))
            orden_id = cursor.lastrowid

        for item in items:
            p_id = item['id']
            p_nombre = item['nombre']
            p_precio = float(item['precio'])
            p_cant = int(item['cantidad'])
            p_subtotal = p_precio * p_cant

            db.execute("""
                INSERT INTO ordenes_abiertas_items (orden_id, producto_id, nombre_producto, precio_unitario, cantidad, subtotal)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (orden_id, p_id, p_nombre, p_precio, p_cant, p_subtotal))

        db.commit()
        return jsonify({'success': True, 'orden_id': orden_id, 'message': 'Orden guardada correctamente.'})
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        db.close()


@app.route('/api/ordenes_abiertas/<int:orden_id>/cobrar', methods=['POST'])
def api_cobrar_orden_abierta(orden_id):
    if 'usuario' not in session:
        return jsonify({'success': False, 'error': 'No autorizado'}), 401

    data = request.get_json() or {}
    metodo_pago = data.get('metodo_pago', 'efectivo')
    efectivo_recibido = float(data.get('efectivo_recibido', 0))
    referencia_pago = data.get('referencia_pago', '')
    cliente_fiado = data.get('cliente_fiado', '')

    usuario_id = session.get('id')
    turno_id = session.get('turno_id') or obtener_turno_activo(usuario_id)

    db = conectar_db()
    try:
        orden = db.execute("SELECT * FROM ordenes_abiertas WHERE id = ? AND estado = 'abierta'", (orden_id,)).fetchone()
        if not orden:
            return jsonify({'success': False, 'error': 'La orden no existe o ya fue cobrada.'}), 404

        items = db.execute("SELECT * FROM ordenes_abiertas_items WHERE orden_id = ?", (orden_id,)).fetchall()
        if not items:
            return jsonify({'success': False, 'error': 'La orden no contiene productos.'}), 400

        total_venta = float(orden['total'])
        fecha_ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        cursor = db.execute("""
            INSERT INTO ventas (vendedor_id, turno_id, fecha, total, metodo_pago, efectivo_recibido, referencia_pago, cliente_fiado)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (usuario_id, turno_id, fecha_ahora, total_venta, metodo_pago, efectivo_recibido, referencia_pago, cliente_fiado))
        venta_id = cursor.lastrowid

        for item in items:
            db.execute("""
                INSERT INTO detalle_ventas (venta_id, producto_id, cantidad, precio_unitario)
                VALUES (?, ?, ?, ?)
            """, (venta_id, item['producto_id'], item['cantidad'], item['precio_unitario']))

            # Descontar stock recetas/insumos
            receta = db.execute("SELECT insumo_id, cantidad_gastada FROM recetas WHERE producto_id = ?", (item['producto_id'],)).fetchall()
            for ing in receta:
                db.execute("""
                    UPDATE insumos
                    SET cantidad_actual = cantidad_actual - ?
                    WHERE id = ?
                """, (item['cantidad'] * ing['cantidad_gastada'], ing['insumo_id']))

        db.execute("UPDATE ordenes_abiertas SET estado = 'pagada' WHERE id = ?", (orden_id,))
        db.commit()

        return jsonify({'success': True, 'venta_id': venta_id, 'message': 'Orden cobrada exitosamente.'})
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        db.close()


@app.route('/api/ordenes_abiertas/<int:orden_id>/cancelar', methods=['POST'])
def api_cancelar_orden_abierta(orden_id):
    if 'usuario' not in session:
        return jsonify({'success': False, 'error': 'No autorizado'}), 401

    db = conectar_db()
    try:
        db.execute("UPDATE ordenes_abiertas SET estado = 'cancelada' WHERE id = ?", (orden_id,))
        db.commit()
        return jsonify({'success': True, 'message': 'Orden cancelada.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        db.close()


@app.route('/turno/cerrar', methods=['POST'])
def finalizar_turno():
    if 'usuario' not in session:
        if request.is_json:
            return jsonify({'success': False, 'error': 'No autorizado'}), 401
        return redirect(url_for('login'))

    turno_id = session.get('turno_id') or obtener_turno_activo(session.get('id'))
    if not turno_id:
        if request.is_json:
            return jsonify({'success': False, 'error': 'No hay turno activo para cerrar.'}), 400
        flash('No hay turno activo para cerrar.', 'error')
        return redirect(request.referrer or url_for('pantalla_ventas'))

    try:
        def parse_float(val):
            if val is not None and val != '':
                try:
                    return float(val)
                except ValueError:
                    pass
            return None

        if request.is_json:
            data = request.get_json() or {}
            efectivo_real = parse_float(data.get('efectivo_real'))
            nequi_real = parse_float(data.get('nequi_real'))
            daviplata_real = parse_float(data.get('daviplata_real'))
            tarjeta_real = parse_float(data.get('tarjeta_real'))
            observaciones = data.get('observaciones')
        else:
            efectivo_real = parse_float(request.form.get('efectivo_real'))
            nequi_real = parse_float(request.form.get('nequi_real'))
            daviplata_real = parse_float(request.form.get('daviplata_real'))
            tarjeta_real = parse_float(request.form.get('tarjeta_real'))
            observaciones = request.form.get('observaciones')

        db = conectar_db()
        try:
            saldos_db = db.execute("""
                WITH movimientos AS (
                    SELECT COALESCE(metodo_pago, 'efectivo') as metodo_pago, SUM(total) as entradas, 0 as salidas
                    FROM ventas
                    WHERE COALESCE(metodo_pago, 'efectivo') != 'fiado'
                    GROUP BY COALESCE(metodo_pago, 'efectivo')
                    UNION ALL
                    SELECT COALESCE(metodo_pago, 'efectivo') as metodo_pago, SUM(monto) as entradas, 0 as salidas
                    FROM abonos_deuda
                    GROUP BY COALESCE(metodo_pago, 'efectivo')
                    UNION ALL
                    SELECT COALESCE(metodo_pago, 'efectivo') as metodo_pago, 0 as entradas, SUM(monto) as salidas
                    FROM gastos
                    GROUP BY COALESCE(metodo_pago, 'efectivo')
                )
                SELECT
                    metodo_pago,
                    (SUM(entradas) - SUM(salidas)) as saldo
                FROM movimientos
                GROUP BY metodo_pago
            """).fetchall()
        finally:
            db.close()

        saldos_esperados = {'efectivo': 0.0, 'nequi': 0.0, 'daviplata': 0.0, 'tarjeta': 0.0}
        for fila in saldos_db:
            metodo = str(fila['metodo_pago']).lower()
            if metodo in saldos_esperados:
                saldos_esperados[metodo] = float(fila['saldo'] or 0)

        efectivo_esperado = saldos_esperados['efectivo']
        nequi_esperado = saldos_esperados['nequi']
        daviplata_esperado = saldos_esperados['daviplata']
        tarjeta_esperado = saldos_esperados['tarjeta']

        diferencia_efectivo = (efectivo_real - efectivo_esperado) if efectivo_real is not None else None
        diferencia_nequi = (nequi_real - nequi_esperado) if nequi_real is not None else None
        diferencia_daviplata = (daviplata_real - daviplata_esperado) if daviplata_real is not None else None
        diferencia_tarjeta = (tarjeta_real - tarjeta_esperado) if tarjeta_real is not None else None

        cerrar_turno(
            turno_id=turno_id,
            usuario_id=session.get('id'),
            efectivo_esperado=efectivo_esperado, efectivo_real=efectivo_real, diferencia=diferencia_efectivo,
            nequi_esperado=nequi_esperado, nequi_real=nequi_real, diferencia_nequi=diferencia_nequi,
            daviplata_esperado=daviplata_esperado, daviplata_real=daviplata_real, diferencia_daviplata=diferencia_daviplata,
            tarjeta_esperado=tarjeta_esperado, tarjeta_real=tarjeta_real, diferencia_tarjeta=diferencia_tarjeta,
            observaciones=observaciones
        )
        
        pdf_filename = None
        json_filename = None
        try:
            archivos_cierre = generar_archivos_cierre_turno(turno_id)
            pdf_filename = archivos_cierre['pdf']
            json_filename = archivos_cierre['json']
            flash(
                'Cierre generado. PDF: /reportes_cierre/{0} | JSON: /reportes_cierre/{1}'.format(
                    pdf_filename,
                    json_filename
                ),
                'success'
            )
        except Exception as e:
            flash(f'El turno se cerró, pero no se pudo generar reporte: {str(e)}', 'error')

        session['turno_id'] = None

        if request.is_json:
            return jsonify({
                'success': True,
                'message': '✅ Turno cerrado correctamente.',
                'pdf_url': f'/reportes_cierre/{pdf_filename}' if pdf_filename else None,
                'json_url': f'/reportes_cierre/{json_filename}' if json_filename else None,
                'efectivo_esperado': efectivo_esperado,
                'efectivo_real': efectivo_real,
                'diferencia': diferencia_efectivo
            })

        flash('✅ Turno cerrado correctamente.', 'success')
        return redirect(request.referrer or url_for('pantalla_ventas'))
    except Exception as e:
        if request.is_json:
            return jsonify({'success': False, 'error': str(e)}), 500
        flash(f'Error al cerrar turno: {str(e)}', 'error')
        return redirect(request.referrer or url_for('pantalla_ventas'))

# ==========================================
# 3. PANTALLA DE VENTAS
# ==========================================
@app.route('/ventas')
def pantalla_ventas():
    if 'usuario' not in session:
        return redirect(url_for('login'))
        
    db = conectar_db()
    
    categorias = [dict(c) for c in db.execute("SELECT id, nombre_categoria, icono FROM categorias ORDER BY nombre_categoria ASC;").fetchall()]
    insumos = [dict(i) for i in db.execute("SELECT id, nombre_insumo, unidad_medida FROM insumos ORDER BY nombre_insumo ASC;").fetchall()]
    productos = db.execute("""
        SELECT DISTINCT p.id, p.nombre, p.es_pack, p.unidades_por_pack, p.categoria_id,
               COALESCE(c.nombre_categoria, 'Sin categoría') AS categoria_nombre
        FROM productos p
        LEFT JOIN categorias c ON c.id = p.categoria_id
        ORDER BY p.nombre ASC
    """).fetchall()
    clientes_exclusivos = db.execute("""
        SELECT id, nombre
        FROM clientes_exclusivos
        WHERE activo = 1
        ORDER BY nombre ASC
    """).fetchall()

    categorias_json = {str(cat['id']): cat['nombre_categoria'] for cat in categorias}
    productos_compra_json = []
    for p in productos:
        d = dict(p)
        try:
            d['categoria_id'] = int(p['categoria_id']) if (p['categoria_id'] is not None and str(p['categoria_id']).strip() != '') else None
        except (ValueError, TypeError):
            d['categoria_id'] = None
        productos_compra_json.append(d)
        
    clientes_exclusivos_json = [dict(c) for c in clientes_exclusivos]
    
    db.close()
    
    return render_template('ventas.html', 
                           nombre=session['nombre'], 
                           rol=session['rol'], 
                           categorias=categorias,
                           categorias_json=categorias_json,
                           insumos_disponibles=insumos,
                           productos_disponibles=productos,
                           productos_compra_json=productos_compra_json,
                           clientes_exclusivos_json=clientes_exclusivos_json)

# ==========================================
# 4. PANEL DE ADMINISTRADOR GENERAL
# ==========================================
@app.route('/admin')
def panel_administrador():
    if 'usuario' not in session or session['rol'] != 'administrador':
        return redirect(url_for('login'))
    
    db = conectar_db()
    
    raw_productos = db.execute("""
         SELECT p.id, p.nombre, p.precio, p.cantidad_stock, p.es_preparado, 
               p.es_pack, p.unidades_por_pack, p.categoria_id, p.codigo_barras,
               p.precio_unidad_venta, p.precio_medio_venta, c.nombre_categoria 
        FROM productos p
        LEFT JOIN categorias c ON p.categoria_id = c.id
    """).fetchall()
    
    productos_procesados = []
    for p in raw_productos:
        try:
            cat_id = int(p['categoria_id']) if (p['categoria_id'] is not None and str(p['categoria_id']).strip() != '') else None
        except (ValueError, TypeError):
            cat_id = None
            
        productos_procesados.append({
            'id': p['id'],
            'nombre': p['nombre'],
            'precio': p['precio'],
            'cantidad_stock': p['cantidad_stock'],
            'es_preparado': p['es_preparado'],
            'nombre_categoria': p['nombre_categoria'],
            'es_pack': p['es_pack'] if p['es_pack'] is not None else 0,
            'unidades_por_pack': p['unidades_por_pack'] if p['unidades_por_pack'] is not None else 0,
            'categoria_id': cat_id if cat_id is not None else '',
            'codigo_barras': p['codigo_barras'] if 'codigo_barras' in p.keys() else '',
            'precio_unidad_venta': p['precio_unidad_venta'] if 'precio_unidad_venta' in p.keys() else None,
            'precio_medio_venta': p['precio_medio_venta'] if 'precio_medio_venta' in p.keys() else None
        })
    
    categorias = [dict(c) for c in db.execute("SELECT * FROM categorias").fetchall()]
    usuarios = [dict(u) for u in db.execute("SELECT id, nombre, usuario, rol FROM usuarios").fetchall()]
    insumos = [dict(i) for i in db.execute("SELECT * FROM insumos").fetchall()]
    clientes_exclusivos = db.execute("""
        SELECT id, nombre, COALESCE(fecha_creacion, DATETIME('now', 'localtime')) as fecha_creacion
        FROM clientes_exclusivos
        WHERE activo = 1
        ORDER BY nombre ASC
    """).fetchall()
    
    historial_gastos = db.execute("""
        SELECT fecha, categoria_gasto, descripcion, monto, metodo_pago 
        FROM gastos 
        WHERE date(fecha) = date('now', 'localtime')
        ORDER BY fecha DESC
    """).fetchall()
    
    # Caja física actual: solo efectivo, acumulado real del sistema.
    resultado_saldo = db.execute("""
        SELECT
            COALESCE((
                SELECT SUM(total)
                FROM ventas
                WHERE COALESCE(metodo_pago, 'efectivo') = 'efectivo'
            ), 0)
            +
            COALESCE((
                SELECT SUM(monto)
                FROM abonos_deuda
                WHERE COALESCE(metodo_pago, 'efectivo') = 'efectivo'
            ), 0)
            -
            COALESCE((
                SELECT SUM(monto)
                FROM gastos
                WHERE COALESCE(metodo_pago, 'efectivo') = 'efectivo'
            ), 0) AS saldo_caja_neto
    """).fetchone()
    
    saldo_actual = resultado_saldo['saldo_caja_neto'] if (resultado_saldo and 'saldo_caja_neto' in resultado_saldo.keys()) else 0
    total_productos_inventario = 0
    total_valor_inventario = 0.0
    for p in raw_productos:
        es_preparado = p['es_preparado'] or 0
        if not es_preparado:
            total_productos_inventario += 1
            stock = p['cantidad_stock'] or 0.0
            precio = p['precio'] or 0.0
            total_valor_inventario += (stock * precio)

    db.close()
    
    return render_template('admin.html', 
                           productos=productos_procesados, 
                           categorias=categorias, 
                           insumos=insumos, 
                           nombre=session['nombre'],
                           usuarios_empleados=usuarios,
                           clientes_exclusivos=clientes_exclusivos,
                           historial_gastos=historial_gastos,
                           saldo_caja=saldo_actual,
                           total_productos_inventario=total_productos_inventario,
                           total_valor_inventario=total_valor_inventario,
                           version_actual=VERSION)


@app.route('/admin/reset_all_data', methods=['POST'])
def reset_all_data():
    if 'usuario' not in session or session['rol'] != 'administrador':
        return redirect(url_for('login'))

    db = conectar_db()
    try:
        db.execute("BEGIN TRANSACTION;")
        tablas = ['detalle_ventas', 'abonos_deuda', 'deudas_clientes', 'ventas', 'gastos', 'caja_movimientos', 'turnos']
        for tabla in tablas:
            db.execute(f"DELETE FROM {tabla};")

        try:
            db.execute("DELETE FROM sqlite_sequence WHERE name IN ('detalle_ventas', 'abonos_deuda', 'deudas_clientes', 'ventas', 'gastos', 'caja_movimientos', 'turnos');")
        except Exception:
            pass

        db.commit()
        if 'turno_id' in session:
            session['turno_id'] = abrir_turno({'id': session.get('id'), 'nombre': session.get('nombre'), 'rol': session.get('rol')})
        flash('✅ Caja, ventas, gastos y turnos reiniciados correctamente.', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Error al reiniciar datos: {str(e)}', 'error')
    finally:
        db.close()

    return redirect(url_for('panel_administrador'))


@app.route('/admin/eliminar_insumo/<int:id>', methods=['POST'])
def eliminar_insumo(id):
    if 'usuario' not in session or session['rol'] != 'administrador':
        return redirect(url_for('login'))
        
    db = conectar_db()
    try:
        # Borrado físico directo
        db.execute("DELETE FROM insumos WHERE id = ?", (id,))
        db.commit()
    except Exception as e:
        print(f"WARN: No se pudo eliminar el insumo (posiblemente esta en una receta): {e}")
    finally:
        db.close()
        
    return redirect(url_for('panel_administrador'))

@app.route('/admin/eliminar_proveedor/<int:id>', methods=['POST'])
def eliminar_proveedor(id):
    if 'usuario' not in session or session['rol'] != 'administrador':
        return redirect(url_for('login'))
        
    db = conectar_db()
    try:
        db.execute("DELETE FROM proveedores WHERE id = ?", (id,))
        db.commit()
    except Exception as e:
        print(f"WARN: Error al eliminar proveedor: {e}")
    finally:
        db.close()
        
    return redirect(url_for('panel_administrador'))

@app.route('/admin/eliminar_usuario/<int:id>', methods=['POST'])
def eliminar_usuario(id):
    if 'usuario' not in session or session['rol'] != 'administrador':
        return redirect(url_for('login'))
        
    db = conectar_db()
    try:
        db.execute("DELETE FROM usuarios WHERE id = ?", (id,))
        db.commit()
    except Exception as e:
        print(f"WARN: Error al eliminar usuario: {e}")
    finally:
        db.close()
        
    return redirect(url_for('panel_administrador'))

@app.route('/admin/editar_usuario/<int:id>', methods=['POST'])
def editar_usuario(id):
    if 'usuario' not in session or session['rol'] != 'administrador': 
        return redirect(url_for('login'))
        
    nombre = request.form.get('nombre')
    user = request.form.get('usuario')
    rol = request.form.get('rol')
    
    db = conectar_db()
    # Ajusta los nombres de las columnas (ej: 'nombre', 'usuario', 'rol') según tu DB
    db.execute("""
        UPDATE usuarios 
        SET nombre = ?, usuario = ?, rol = ? 
        WHERE id = ?
    """, (nombre, user, rol, id))
    
    db.commit()
    db.close()
    
    flash("Usuario actualizado correctamente", "success")
    return redirect(url_for('panel_administrador'))
    
@app.route('/admin/editar_insumo/<int:id>', methods=['POST'])
def editar_insumo(id):
    if 'usuario' not in session or session['rol'] != 'administrador':
        return redirect(url_for('login'))
        
    nuevo_nombre = request.form['nombre_insumo'].strip()
    nueva_cantidad = float(request.form['cantidad_actual'])
    
    db = conectar_db()
    try:
        db.execute("""
            UPDATE insumos 
            SET nombre_insumo = ?, cantidad_actual = ? 
            WHERE id = ?
        """, (nuevo_nombre, nueva_cantidad, id))
        db.commit()
    except Exception as e:
        print(f"WARN: Error al editar insumo: {e}")
    finally:
        db.close()
        
    return redirect(url_for('panel_administrador'))

# ==========================================
# 5. PANEL DE INFORMES Y REPORTES
# ==========================================
@app.route('/admin/informes')
def ver_informes():
    if 'usuario' not in session or session['rol'] != 'administrador':
        return redirect(url_for('login', error="Acceso denegado. Solo administradores."))
        
    try:
        db = conectar_db()
        
        # 1. Consulta General
        reporte_general = db.execute("""
            SELECT 
                COUNT(v.id) as transacciones,
                IFNULL(SUM(v.total), 0) as venta_total,
                (SELECT IFNULL(SUM(monto), 0) FROM gastos) as total_gastos,
                (IFNULL(SUM(v.total), 0) - (SELECT IFNULL(SUM(monto), 0) FROM gastos)) as balance_neto
            FROM ventas v;
        """).fetchone()
        
        # 2. Consulta por Vendedor
        reporte_vendedores = db.execute("""
            WITH resumen_producto AS (
                SELECT
                    u.id as empleado_id,
                    u.nombre as empleado_nombre,
                    u.rol as empleado_rol,
                    IFNULL(p.nombre, 'Sin ventas') as producto_nombre,
                    IFNULL(SUM(dv.cantidad), 0) as unidades_producto,
                    COUNT(DISTINCT v.id) as cantidad_ventas,
                    IFNULL(SUM(dv.cantidad * dv.precio_unitario), 0) as total_recaudado
                FROM usuarios u
                LEFT JOIN ventas v ON u.id = v.vendedor_id
                LEFT JOIN detalle_ventas dv ON v.id = dv.venta_id
                LEFT JOIN productos p ON dv.producto_id = p.id
                WHERE COALESCE(v.tipo_venta, 'producto') = 'producto' OR v.id IS NULL
                GROUP BY u.id, p.id
            ),
            resumen_intangible AS (
                SELECT
                    u.id as empleado_id,
                    u.nombre as empleado_nombre,
                    u.rol as empleado_rol,
                    'Ingreso intangible' as producto_nombre,
                    COUNT(v.id) as unidades_producto,
                    COUNT(v.id) as cantidad_ventas,
                    COALESCE(SUM(v.total), 0) as total_recaudado
                FROM usuarios u
                JOIN ventas v ON u.id = v.vendedor_id
                WHERE COALESCE(v.tipo_venta, 'producto') = 'intangible'
                GROUP BY u.id, u.nombre, u.rol
            )
            SELECT * FROM resumen_producto
            UNION ALL
            SELECT * FROM resumen_intangible
            ORDER BY empleado_nombre ASC, unidades_producto DESC;
        """).fetchall()

        # 3. Ranking de cantidad vendida y recaudación por producto real
        reporte_productos = db.execute("""
            WITH productos_tangibles AS (
                SELECT
                    p.nombre as producto_nombre,
                    SUM(dv.cantidad) as unidades_vendidas,
                    SUM(dv.cantidad * dv.precio_unitario) as total_recaudado
                FROM detalle_ventas dv
                JOIN productos p ON dv.producto_id = p.id
                JOIN ventas v ON v.id = dv.venta_id
                WHERE COALESCE(v.tipo_venta, 'producto') = 'producto'
                GROUP BY p.id
            ),
            productos_intangibles AS (
                SELECT
                    CASE
                        WHEN INSTR(COALESCE(v.referencia_pago, ''), '|') > 0 THEN
                            TRIM(SUBSTR(v.referencia_pago, 1, INSTR(v.referencia_pago, '|') - 1)) ||
                            CASE
                                WHEN TRIM(SUBSTR(v.referencia_pago, INSTR(v.referencia_pago, '|') + 1)) != '' THEN
                                    ' (Ref: ' || TRIM(SUBSTR(v.referencia_pago, INSTR(v.referencia_pago, '|') + 1)) || ')'
                                ELSE ''
                            END
                        WHEN TRIM(COALESCE(v.referencia_pago, '')) != '' THEN
                            TRIM(v.referencia_pago)
                        ELSE
                            'Ingreso intangible'
                    END as producto_nombre,
                    COUNT(v.id) as unidades_vendidas,
                    COALESCE(SUM(v.total), 0) as total_recaudado
                FROM ventas v
                WHERE COALESCE(v.tipo_venta, 'producto') = 'intangible'
                GROUP BY producto_nombre
            )
            SELECT * FROM productos_tangibles
            UNION ALL
            SELECT * FROM productos_intangibles
            ORDER BY unidades_vendidas DESC, total_recaudado DESC;
        """).fetchall()

        # 4. Resumen analítico por método de pago (Agrupado)
        reporte_pagos = db.execute("""
            SELECT 
                metodo_pago,
                COUNT(id) as cantidad_transacciones,
                SUM(total) as total_recaudado
            FROM ventas
            GROUP BY metodo_pago;
        """).fetchall()
        
        # 5. Historial detallado de pagos digitales (uno por uno)
        reporte_nequi_detallado = db.execute("""
            SELECT 
                v.id as venta_id,
                v.fecha,
                u.nombre as cajero,
                v.total,
                v.metodo_pago,
                IFNULL(v.referencia_pago, 'Sin número registrado') as referencia
            FROM ventas v
            LEFT JOIN usuarios u ON v.vendedor_id = u.id
            WHERE v.metodo_pago IN ('nequi', 'daviplata', 'tarjeta')
            ORDER BY v.fecha DESC;
        """).fetchall()

        # 6. Resumen por turno (ventas, gastos, saldo y movimientos)
        reporte_turnos = db.execute("""
            SELECT
                t.id as turno_id,
                t.nombre_usuario,
                t.rol,
                t.fecha_apertura,
                COALESCE(t.fecha_cierre, 'Turno activo') as fecha_cierre,
                t.estado,
                t.efectivo_esperado, t.efectivo_real, t.diferencia,
                t.nequi_esperado, t.nequi_real, t.diferencia_nequi,
                t.daviplata_esperado, t.daviplata_real, t.diferencia_daviplata,
                t.tarjeta_esperado, t.tarjeta_real, t.diferencia_tarjeta,
                t.observaciones,
                COALESCE((SELECT SUM(v.total) FROM ventas v WHERE v.turno_id = t.id), 0) as ventas_turno,
                COALESCE((SELECT SUM(g.monto) FROM gastos g WHERE g.turno_id = t.id), 0) as gastos_turno,
                COALESCE((SELECT COUNT(*) FROM caja_movimientos m WHERE m.turno_id = t.id), 0) as movimientos_turno
            FROM turnos t
            ORDER BY t.id DESC
            LIMIT 40;
        """).fetchall()

        # 7. Saldos por tipo de cuenta (método de pago)
        saldos_cuentas = db.execute("""
            WITH movimientos AS (
                SELECT COALESCE(metodo_pago, 'efectivo') as metodo_pago, SUM(total) as entradas, 0 as salidas
                FROM ventas
                WHERE COALESCE(metodo_pago, 'efectivo') != 'fiado'
                GROUP BY COALESCE(metodo_pago, 'efectivo')
                UNION ALL
                SELECT COALESCE(metodo_pago, 'efectivo') as metodo_pago, SUM(monto) as entradas, 0 as salidas
                FROM abonos_deuda
                GROUP BY COALESCE(metodo_pago, 'efectivo')
                UNION ALL
                SELECT COALESCE(metodo_pago, 'efectivo') as metodo_pago, 0 as entradas, SUM(monto) as salidas
                FROM gastos
                GROUP BY COALESCE(metodo_pago, 'efectivo')
            )
            SELECT
                metodo_pago,
                SUM(entradas) as total_entradas,
                SUM(salidas) as total_salidas,
                (SUM(entradas) - SUM(salidas)) as saldo_actual
            FROM movimientos
            GROUP BY metodo_pago
            ORDER BY metodo_pago ASC;
        """).fetchall()

        deuda_fiado_total = db.execute("""
            SELECT COALESCE(SUM(saldo_pendiente), 0) as total_deuda
            FROM deudas_clientes
            WHERE estado = 'pendiente'
        """).fetchone()

        deuda_por_deudor = db.execute("""
            SELECT
                dc.cliente_nombre,
                COUNT(dc.id) as cantidad_ventas_fiadas,
                COALESCE(SUM(dc.monto_total), 0) as monto_total_fiado,
                COALESCE(SUM(dc.monto_pagado), 0) as monto_pagado,
                COALESCE(SUM(dc.saldo_pendiente), 0) as saldo_pendiente,
                MAX(dc.fecha_actualizacion) as ultima_actualizacion
            FROM deudas_clientes dc
            WHERE dc.estado = 'pendiente'
            GROUP BY dc.cliente_nombre
            ORDER BY saldo_pendiente DESC, dc.cliente_nombre ASC
        """).fetchall()

        detalle_fiado_por_producto = db.execute("""
            SELECT
                dc.cliente_nombre,
                p.nombre as producto_nombre,
                COALESCE(SUM(dv.cantidad), 0) as unidades_fiadas,
                COALESCE(SUM(dv.cantidad * dv.precio_unitario), 0) as total_fiado,
                MAX(v.fecha) as ultima_venta
            FROM deudas_clientes dc
            JOIN ventas v ON v.id = dc.venta_id
            JOIN detalle_ventas dv ON dv.venta_id = v.id
            JOIN productos p ON p.id = dv.producto_id
            WHERE dc.estado = 'pendiente'
            GROUP BY dc.cliente_nombre, p.id, p.nombre
            ORDER BY dc.cliente_nombre ASC, total_fiado DESC, p.nombre ASC
        """).fetchall()

        # 8. Saldo actual de caja física (solo efectivo)
        caja_efectivo = db.execute("""
            SELECT
                COALESCE((
                    SELECT SUM(total)
                    FROM ventas
                    WHERE COALESCE(metodo_pago, 'efectivo') = 'efectivo'
                ), 0)
                +
                COALESCE((
                    SELECT SUM(monto)
                    FROM abonos_deuda
                    WHERE COALESCE(metodo_pago, 'efectivo') = 'efectivo'
                ), 0)
                -
                COALESCE((
                    SELECT SUM(monto)
                    FROM gastos
                    WHERE COALESCE(metodo_pago, 'efectivo') = 'efectivo'
                ), 0) AS caja_efectivo
        """).fetchone()

        # 9. Producto que más se está agotando (menor stock)
        producto_agotando = db.execute("""
            SELECT
                p.nombre as nombre_producto,
                COALESCE(p.cantidad_stock, 0) as stock_actual
            FROM productos p
            WHERE COALESCE(p.es_preparado, 0) = 0
            ORDER BY COALESCE(p.cantidad_stock, 0) ASC, p.nombre ASC
            LIMIT 1
        """).fetchone()

        # 10. Empleado con mayor acumulado de ventas
        empleado_top_ventas = db.execute("""
            SELECT
                u.nombre as nombre_empleado,
                COALESCE(SUM(v.total), 0) as total_ventas
            FROM usuarios u
            LEFT JOIN ventas v ON v.vendedor_id = u.id
            GROUP BY u.id, u.nombre
            ORDER BY total_ventas DESC, u.nombre ASC
            LIMIT 1
        """).fetchone()

        # 11. Gastos detallados
        gastos_detallados = db.execute("""
            SELECT
                g.fecha,
                COALESCE(u.nombre, 'Sin usuario') as responsable,
                COALESCE(g.metodo_pago, 'efectivo') as metodo_pago,
                COALESCE(g.categoria_gasto, 'Otros') as categoria_gasto,
                g.descripcion,
                g.monto
            FROM gastos g
            LEFT JOIN usuarios u ON u.id = g.vendedor_id
            ORDER BY g.fecha DESC
            LIMIT 200
        """).fetchall()
        
        db.close()
        
        return render_template(
            'informes.html', 
            general=reporte_general, 
            vendedores=reporte_vendedores,
            productos_vendidos=reporte_productos, 
            reporte_pagos=reporte_pagos,
            nequi_detallado=reporte_nequi_detallado,
            reporte_turnos=reporte_turnos,
            saldos_cuentas=saldos_cuentas,
            caja_efectivo=(caja_efectivo['caja_efectivo'] if caja_efectivo else 0),
            deuda_fiado_total=(deuda_fiado_total['total_deuda'] if deuda_fiado_total else 0),
            deuda_por_deudor=deuda_por_deudor,
            detalle_fiado_por_producto=detalle_fiado_por_producto,
            producto_agotando=producto_agotando,
            empleado_top_ventas=empleado_top_ventas,
            gastos_detallados=gastos_detallados,
            nombre=session['nombre'], 
            rol=session['rol']
        )
        
    except Exception as e:
        return f"Error crítico al estructurar los informes analíticos: {str(e)}", 500
    
# ==========================================
# 6. GESTIÓN DE PERFIL Y USUARIOS (ADMIN)
# ==========================================
@app.route('/admin/editar_perfil', methods=['POST'])
def editar_perfil():
    if 'usuario' not in session or session['rol'] != 'administrador': 
        return redirect(url_for('login'))
    
    nuevo_nombre = request.form['nombre']
    nueva_clave = request.form['contrasena']
    usuario_actual = session['usuario']
    
    db = conectar_db()
    if nueva_clave.strip():
        db.execute("UPDATE usuarios SET nombre = ?, contrasena = ? WHERE usuario = ?", (nuevo_nombre, nueva_clave, usuario_actual))
    else:
        db.execute("UPDATE usuarios SET nombre = ? WHERE usuario = ?", (nuevo_nombre, usuario_actual))
    db.commit()
    db.close()
    
    session['nombre'] = nuevo_nombre
    return redirect(url_for('panel_administrador'))

@app.route('/admin/add_usuario', methods=['POST'])
def add_usuario():
    if 'usuario' not in session or session['rol'] != 'administrador': 
        return redirect(url_for('login'))
    
    nombre = request.form['nombre']
    usuario = request.form['usuario']
    contrasena = request.form['contrasena']
    rol = request.form['rol']
    
    db = conectar_db()
    try:
        db.execute("INSERT INTO usuarios (nombre, usuario, contrasena, rol) VALUES (?, ?, ?, ?)", 
                   (nombre, usuario, contrasena, rol))
        db.commit()
        flash("Usuario agregado correctamente", "success")
    except sqlite3.IntegrityError:
        flash("El usuario ya existe", "error")
    finally:
        db.close()
    return redirect(url_for('panel_administrador'))


@app.route('/admin/add_cliente_exclusivo', methods=['POST'])
def add_cliente_exclusivo():
    if 'usuario' not in session or session['rol'] != 'administrador':
        return redirect(url_for('login'))

    nombre = (request.form.get('nombre_cliente') or '').strip()
    if not nombre:
        flash('Debes ingresar el nombre del cliente exclusivo', 'error')
        return redirect(url_for('panel_administrador'))

    db = conectar_db()
    try:
        db.execute("""
            INSERT INTO clientes_exclusivos (nombre, activo, fecha_creacion)
            VALUES (?, 1, DATETIME('now', 'localtime'))
        """, (nombre,))
        db.commit()
        flash('Cliente exclusivo registrado correctamente', 'success')
    except sqlite3.IntegrityError:
        flash('Ese cliente exclusivo ya está registrado', 'error')
    except Exception as e:
        db.rollback()
        flash(f'Error al registrar cliente exclusivo: {str(e)}', 'error')
    finally:
        db.close()

    return redirect(url_for('panel_administrador'))


@app.route('/admin/eliminar_cliente_exclusivo/<int:id_cliente>', methods=['POST'])
def eliminar_cliente_exclusivo(id_cliente):
    if 'usuario' not in session or session['rol'] != 'administrador':
        return redirect(url_for('login'))

    db = conectar_db()
    try:
        db.execute("UPDATE clientes_exclusivos SET activo = 0 WHERE id = ?", (id_cliente,))
        db.commit()
        flash('Cliente exclusivo eliminado', 'success')
    except Exception as e:
        db.rollback()
        flash(f'No se pudo eliminar el cliente exclusivo: {str(e)}', 'error')
    finally:
        db.close()

    return redirect(url_for('panel_administrador'))

# ==========================================
# GESTIÓN UNIFICADA DE GASTOS Y STOCK
# ==========================================

# ==========================================
# RUTAS CORREGIDAS Y LIMPIAS
# ==========================================

@app.route('/admin/add_categoria', methods=['POST'])
def add_categoria():
    if 'usuario' not in session or session['rol'] != 'administrador':
        return redirect(url_for('login'))
        
    nueva_categoria = request.form.get('nombre_categoria')
    icono = request.form.get('icono_categoria', '').strip() or None
    if not nueva_categoria:
        return redirect(url_for('panel_administrador'))
    
    db = conectar_db()
    try:
        # Asegúrate de que el nombre de columna en tu tabla 'categorias' sea 'nombre_categoria'
        db.execute("INSERT INTO categorias (nombre_categoria, icono) VALUES (?, ?)", (nueva_categoria, icono))
        db.commit()
        flash('Categoría agregada con éxito', 'success')
    except Exception as e:
        print(f"Error: {e}")
        flash('Error: La categoría ya existe o hubo un fallo', 'error')
    finally:
        db.close()
    return redirect(url_for('panel_administrador'))

# ==========================================
# 🔄 ACTUALIZACIONES AUTOMÁTICAS
# ==========================================
@app.route('/admin/verificar_version', methods=['GET'])
def verificar_version():
    if 'usuario' not in session or session['rol'] != 'administrador':
        return jsonify({"error": "No autorizado"}), 403
        
    import urllib.request
    
    import time
    URL_VERSION = f"https://raw.githubusercontent.com/EEMC-369/cafeto24/refs/heads/main/version.json?t={int(time.time())}"
    
    try:
        req = urllib.request.Request(URL_VERSION, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=3) as response:
            data = json.loads(response.read().decode('utf-8'))
            
        nueva_version = data.get("version", "2.0.0")
        url_descarga = data.get("url", "")
        
        # Separar por puntos y convertir a números para comparación correcta
        v_actual = tuple(map(int, VERSION.split('.')))
        v_nueva = tuple(map(int, nueva_version.split('.')))
        
        actualizacion_disponible = v_nueva > v_actual
        
        return jsonify({
            "actual_version": VERSION,
            "nueva_version": nueva_version,
            "actualizacion_disponible": actualizacion_disponible,
            "url_descarga": url_descarga
        })
    except Exception as e:
        return jsonify({
            "error": f"No se pudo conectar al servidor de actualizaciones: {e}",
            "actual_version": VERSION,
            "actualizacion_disponible": False
        })

@app.route('/admin/actualizar_software', methods=['POST'])
def actualizar_software():
    if 'usuario' not in session or session['rol'] != 'administrador':
        return redirect(url_for('login'))
        
    url_descarga = request.form.get('url_descarga')
    if not url_descarga:
        flash("URL de descarga no válida.", "error")
        return redirect(url_for('panel_administrador'))
        
    import urllib.request
    import subprocess
    
    if es_compilado:
        exe_dir = os.path.dirname(sys.executable)
        exe_path = sys.executable
    else:
        exe_dir = os.path.dirname(os.path.abspath(__file__))
        exe_path = os.path.join(exe_dir, "CajaCafeto24.py")
        
    is_setup = "setup" in url_descarga.lower()
    import tempfile
    
    if is_setup:
        dest_new_exe = os.path.join(tempfile.gettempdir(), "Cafeto24_Setup_Update.exe")
    else:
        dest_new_exe = os.path.join(exe_dir, "CajaCafeto24_new.exe" if es_compilado else "CajaCafeto24_new.py")
    
    try:
        # Descargar nueva versión
        req = urllib.request.Request(url_descarga, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            with open(dest_new_exe, 'wb') as f:
                f.write(response.read())
                
        # Crear script batch autolimpiable para reemplazar archivos en caliente o ejecutar instalador
        bat_path = os.path.join(tempfile.gettempdir(), "actualizar.bat")
        with open(bat_path, 'w', encoding='utf-8') as f:
            if is_setup:
                f.write(f"""@echo off
title Actualizando Cafeto24...
echo Esperando a que Cafeto24 se cierre...
timeout /t 2 /nobreak > NUL
echo Ejecutando instalador de actualizacion...
start "" "{dest_new_exe}"
del "%~f0"
""")
            else:
                f.write(f"""@echo off
title Actualizando Cafeto24...
echo Esperando a que Cafeto24 se cierre...
timeout /t 2 /nobreak > NUL
del /f /q "{exe_path}"
ren "{dest_new_exe}" "{os.path.basename(exe_path)}"
echo Iniciando nueva version...
start "" "{exe_path}"
del "%~f0"
""")
        # Ejecutar script asíncronamente en Windows
        subprocess.Popen(["cmd.exe", "/c", bat_path], cwd=exe_dir, shell=True)
        # Salida abrupta del proceso para liberar el ejecutable actual
        os._exit(0)
    except Exception as e:
        flash(f"Fallo en la descarga de actualización: {e}", "error")
        
    return redirect(url_for('panel_administrador'))

# ==========================================
# EXPORTACIÓN E IMPORTACIÓN DE DATOS (CSV)
# ==========================================
@app.route('/admin/exportar/productos')
def exportar_productos():
    if 'usuario' not in session or session['rol'] != 'administrador':
        return redirect(url_for('login'))
        
    db = conectar_db()
    try:
        productos = db.execute("""
            SELECT p.id, p.nombre, p.precio, p.cantidad_stock, p.es_pack, 
                   p.unidades_por_pack, p.es_preparado, p.codigo_barras, 
                   p.precio_unidad_venta, p.precio_medio_venta, c.nombre_categoria
            FROM productos p
            LEFT JOIN categorias c ON p.categoria_id = c.id
        """).fetchall()
        
        import io
        import csv
        
        output = io.StringIO()
        output.write('\ufeff') # UTF-8 BOM para Excel
        writer = csv.writer(output, delimiter=';')
        
        writer.writerow([
            'ID', 'Nombre', 'Precio', 'Stock', 'Es Pack (1/0)', 
            'Unidades por Pack', 'Es Preparado (1/0)', 'Código de Barras', 
            'Precio Unidad Venta', 'Precio Medio Venta', 'Categoría'
        ])
        
        for p in productos:
            writer.writerow([
                p['id'], p['nombre'], p['precio'], p['cantidad_stock'], p['es_pack'],
                p['unidades_por_pack'], p['es_preparado'], p['codigo_barras'] or '',
                p['precio_unidad_venta'] or '', p['precio_medio_venta'] or '',
                p['nombre_categoria'] or ''
            ])
            
        response = app.response_class(
            output.getvalue(),
            mimetype='text/csv',
            headers={"Content-disposition": "attachment; filename=productos_exportados.csv"}
        )
        return response
    except Exception as e:
        flash(f"Error al exportar productos: {e}", "error")
        return redirect(url_for('panel_administrador'))
    finally:
        db.close()


@app.route('/admin/exportar/categorias')
def exportar_categorias():
    if 'usuario' not in session or session['rol'] != 'administrador':
        return redirect(url_for('login'))
        
    db = conectar_db()
    try:
        categorias = db.execute("SELECT id, nombre_categoria FROM categorias").fetchall()
        
        import io
        import csv
        
        output = io.StringIO()
        output.write('\ufeff') # UTF-8 BOM
        writer = csv.writer(output, delimiter=';')
        
        writer.writerow(['ID', 'Nombre de Categoría'])
        
        for c in categorias:
            writer.writerow([c['id'], c['nombre_categoria']])
            
        response = app.response_class(
            output.getvalue(),
            mimetype='text/csv',
            headers={"Content-disposition": "attachment; filename=categorias_exportadas.csv"}
        )
        return response
    except Exception as e:
        flash(f"Error al exportar categorías: {e}", "error")
        return redirect(url_for('panel_administrador'))
    finally:
        db.close()


@app.route('/admin/importar/productos', methods=['POST'])
def importar_productos():
    if 'usuario' not in session or session['rol'] != 'administrador':
        return redirect(url_for('login'))
        
    archivo = request.files.get('archivo_csv')
    if not archivo or archivo.filename == '':
        flash("No seleccionaste ningún archivo para importar.", "error")
        return redirect(url_for('panel_administrador'))
        
    import io
    import csv
    
    nombre_archivo = archivo.filename.lower()
    es_excel = nombre_archivo.endswith('.xlsx') or nombre_archivo.endswith('.xls')
    
    db = conectar_db()
    cursor = db.cursor()
    
    # Función auxiliar para parsear números decimales con formato local ES/US
    def limpiar_numero(val_str):
        if not val_str:
            return 0.0
        val_str = val_str.strip()
        if val_str.endswith('.0'):
            try:
                return float(val_str)
            except ValueError:
                pass
        if ',' in val_str and '.' not in val_str:
            val_str = val_str.replace(',', '.')
        elif ',' in val_str and '.' in val_str:
            if val_str.find(',') < val_str.find('.'):
                val_str = val_str.replace(',', '')
            else:
                val_str = val_str.replace('.', '').replace(',', '.')
        try:
            return float(val_str)
        except ValueError:
            return 0.0

    try:
        filas = []
        if es_excel:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(archivo.read()), data_only=True)
            sheet = wb.active
            for row in sheet.iter_rows(values_only=True):
                filas.append([str(cell) if cell is not None else '' for cell in row])
        else:
            # Leer el contenido del archivo con newline='' para evitar errores de new-line del modulo csv
            contenido = archivo.read().decode('utf-8-sig', errors='ignore')
            stream = io.StringIO(contenido, newline='')
            
            # Detectar el delimitador robustamente por conteo de caracteres
            primer_linea = stream.readline()
            num_semicolons = primer_linea.count(';')
            num_commas = primer_linea.count(',')
            num_tabs = primer_linea.count('\t')
            
            if num_tabs > num_semicolons and num_tabs > num_commas:
                delimitador = '\t'
            elif num_semicolons >= num_commas and num_semicolons > 0:
                delimitador = ';'
            else:
                delimitador = ','
            stream.seek(0)
            
            reader = csv.reader(stream, delimiter=delimitador)
            for row in reader:
                filas.append(row)
                
        if not filas:
            flash("El archivo está vacío.", "error")
            return redirect(url_for('panel_administrador'))
            
        headers = filas[0]
        headers_cleaned = []
        for h in headers:
            h_clean = h.strip().lower()
            h_clean = h_clean.replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')
            headers_cleaned.append(h_clean)
            
        # Mapeo flexible de columnas para archivos POS y archivos de Odoo
        idx_id = next((i for i, h in enumerate(headers_cleaned) if h == 'id'), None)
        idx_nombre = next((i for i, h in enumerate(headers_cleaned) if 'nombre' in h), None)
        idx_precio = next((i for i, h in enumerate(headers_cleaned) if ('precio de venta' in h) or ('precio' in h and 'unidad' not in h and 'medio' not in h)), None)
        idx_stock = next((i for i, h in enumerate(headers_cleaned) if 'cantidad a la mano' in h or 'stock' in h or 'cantidad' in h), None)
        idx_pack = next((i for i, h in enumerate(headers_cleaned) if 'pack' in h), None)
        idx_unidades_pack = next((i for i, h in enumerate(headers_cleaned) if 'unidades por' in h or 'unidades_por_pack' in h), None)
        idx_preparado = next((i for i, h in enumerate(headers_cleaned) if 'preparado' in h), None)
        idx_codigo = next((i for i, h in enumerate(headers_cleaned) if 'referencia interna' in h or 'codigo' in h or 'barras' in h or 'referencia' in h), None)
        idx_precio_u_venta = next((i for i, h in enumerate(headers_cleaned) if 'unidad_venta' in h or 'unidad venta' in h), None)
        idx_precio_m_venta = next((i for i, h in enumerate(headers_cleaned) if 'medio_venta' in h or 'medio venta' in h), None)
        idx_categoria = next((i for i, h in enumerate(headers_cleaned) if 'categoria del producto' in h or 'categoria' in h), None)

        if idx_nombre is None or idx_precio is None:
            flash(f"El archivo debe tener al menos las columnas 'Nombre' y 'Precio de venta' o 'Precio'. Encontradas: {', '.join(headers)}", "error")
            return redirect(url_for('panel_administrador'))
            
        productos_creados = 0
        productos_actualizados = 0
        
        for row in filas[1:]:
            if not row or not any(row):
                continue
                
            nombre = row[idx_nombre].strip()
            if not nombre:
                continue
                
            precio = limpiar_numero(row[idx_precio])
            stock = limpiar_numero(row[idx_stock]) if idx_stock is not None else 0.0
            
            es_pack = 1 if (idx_pack is not None and row[idx_pack].strip() in ('1', 'true', 'True', 'si', 'sí', 'SI', 'SÍ')) else 0
            unidades_por_pack = limpiar_numero(row[idx_unidades_pack]) if idx_unidades_pack is not None else 1.0
            
            es_preparado = 1 if (idx_preparado is not None and row[idx_preparado].strip() in ('1', 'true', 'True', 'si', 'sí', 'SI', 'SÍ')) else 0
            codigo_barras = row[idx_codigo].strip() if (idx_codigo is not None and row[idx_codigo].strip()) else None
            
            precio_unidad_venta = limpiar_numero(row[idx_precio_u_venta]) if (idx_precio_u_venta is not None and row[idx_precio_u_venta].strip()) else None
            precio_medio_venta = limpiar_numero(row[idx_precio_m_venta]) if (idx_precio_m_venta is not None and row[idx_precio_m_venta].strip()) else None

            categoria_id = None
            if idx_categoria is not None:
                cat_nombre = row[idx_categoria].strip()
                if cat_nombre:
                    row_cat = cursor.execute("SELECT id FROM categorias WHERE nombre_categoria = ?", (cat_nombre,)).fetchone()
                    if row_cat:
                        categoria_id = row_cat['id']
                    else:
                        cursor.execute("INSERT INTO categorias (nombre_categoria) VALUES (?)", (cat_nombre,))
                        categoria_id = cursor.lastrowid
            
            prod_id = None
            if idx_id is not None and row[idx_id].strip():
                try:
                    prod_id = int(row[idx_id].strip())
                except ValueError:
                    pass
            
            existe = False
            if prod_id:
                row_prod = cursor.execute("SELECT id FROM productos WHERE id = ?", (prod_id,)).fetchone()
                if row_prod:
                    existe = True
            else:
                # Intentar emparejar por nombre para evitar duplicación
                row_prod = cursor.execute("SELECT id FROM productos WHERE nombre = ?", (nombre,)).fetchone()
                if row_prod:
                    existe = True
                    prod_id = row_prod['id']
            
            if existe:
                cursor.execute("""
                    UPDATE productos
                    SET nombre = ?, precio = ?, cantidad_stock = ?, es_pack = ?, 
                        unidades_por_pack = ?, es_preparado = ?, codigo_barras = ?,
                        precio_unidad_venta = ?, precio_medio_venta = ?, categoria_id = ?
                    WHERE id = ?
                """, (nombre, precio, stock, es_pack, unidades_por_pack, es_preparado,
                      codigo_barras, precio_unidad_venta, precio_medio_venta, categoria_id, prod_id))
                productos_actualizados += 1
            else:
                cursor.execute("""
                    INSERT INTO productos (nombre, precio, cantidad_stock, es_pack, 
                                           unidades_por_pack, es_preparado, codigo_barras,
                                           precio_unidad_venta, precio_medio_venta, categoria_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (nombre, precio, stock, es_pack, unidades_por_pack, es_preparado,
                      codigo_barras, precio_unidad_venta, precio_medio_venta, categoria_id))
                productos_creados += 1
                
        db.commit()
        flash(f"Importación completada: {productos_creados} creados, {productos_actualizados} actualizados.", "success")
    except Exception as e:
        db.rollback()
        flash(f"Error al importar archivo: {e}", "error")
    finally:
        db.close()
        
    return redirect(url_for('panel_administrador'))

# ==========================================
# ✅ CORRECCIÓN: add_producto - Manejo de packs vs stock simple
# ==========================================
@app.route('/admin/add_producto', methods=['POST'])
def add_producto():
    if 'usuario' not in session or session['rol'] != 'administrador':
        return redirect(url_for('login'))
    
    nombre = request.form.get('nombre')
    categoria_id = request.form.get('categoria')
    es_pack = request.form.get('es_pack')
    es_preparado = request.form.get('es_preparado')
    codigo_barras = (request.form.get('codigo_barras') or '').strip() or None
    precio_unidad_venta = request.form.get('precio_unidad_venta')
    precio_medio_venta = request.form.get('precio_medio_venta')

    try:
        precio_unidad_venta = float(precio_unidad_venta) if precio_unidad_venta not in (None, '') else None
    except (TypeError, ValueError):
        precio_unidad_venta = None

    try:
        precio_medio_venta = float(precio_medio_venta) if precio_medio_venta not in (None, '') else None
    except (TypeError, ValueError):
        precio_medio_venta = None
    
    db = conectar_db()
    try:
        if es_pack == '1':
            precio_pack_val = request.form.get('precio_pack')
            precio = float(precio_pack_val) if precio_pack_val and precio_pack_val.strip() else 0.0
            stock_pacas = float(request.form.get('stock_pacas') or 0)
            unidades_por_pack = float(request.form.get('unidades_por_pack') or 1)
            cantidad_stock = stock_pacas * unidades_por_pack
        else:
            precio_val = request.form.get('precio')
            precio = float(precio_val) if precio_val and precio_val.strip() else 0.0
            stock_val = request.form.get('stock')
            cantidad_stock = float(stock_val) if stock_val and stock_val.strip() else 0.0
        
        es_pack_int = 1 if es_pack == '1' else 0
        es_preparado_int = 1 if es_preparado == '1' else 0

        cursor_producto = db.cursor()
        cursor_producto.execute("""
            INSERT INTO productos (nombre, precio, cantidad_stock, es_pack, 
                                   unidades_por_pack, categoria_id, es_preparado, codigo_barras,
                                   precio_unidad_venta, precio_medio_venta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (nombre, precio, cantidad_stock, es_pack_int, 
              request.form.get('unidades_por_pack', 1) if es_pack == '1' else 0,
              categoria_id, es_preparado_int, codigo_barras, precio_unidad_venta, precio_medio_venta))
        
        producto_id = cursor_producto.lastrowid
        
        # Si tiene receta, guardar ingredientes
        if es_preparado == '1':
            insumos_receta = request.form.getlist('insumos_receta[]')
            cantidades_receta = request.form.getlist('cantidades_receta[]')
            
            for insumo_id, cantidad in zip(insumos_receta, cantidades_receta):
                if insumo_id and cantidad:
                    db.execute("""
                        INSERT INTO recetas (producto_id, insumo_id, cantidad_gastada)
                        VALUES (?, ?, ?)
                    """, (producto_id, insumo_id, cantidad))
        
        db.commit()
        flash('Producto creado exitosamente', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Error al crear producto: {str(e)}', 'error')
    finally:
        db.close()
    
    return redirect(url_for('panel_administrador'))


@app.route('/admin/eliminar_producto/<int:id_producto>', methods=['POST'])
def eliminar_producto(id_producto):
    if 'usuario' not in session or session['rol'] != 'administrador': 
        return redirect(url_for('login'))
        
    db = conectar_db()
    try:
        db.execute("BEGIN TRANSACTION;")
        db.execute("DELETE FROM recetas WHERE producto_id = ?", (id_producto,))
        db.execute("DELETE FROM productos WHERE id = ?", (id_producto,))
        db.commit()
        flash("Producto eliminado correctamente", "success")
    except Exception as e:
        db.rollback()
        print(f"Error al eliminar producto: {e}")
        flash("Error al eliminar el producto", "error")
    finally:
        db.close()
        
    return redirect(url_for('panel_administrador'))

# ==========================================
# ✅ CORRECCIÓN: editar_producto - Manejo de packs vs stock simple
# ==========================================
@app.route('/admin/editar_producto/<int:id_producto>', methods=['POST'])
def editar_producto(id_producto):
    if 'usuario' not in session or session['rol'] != 'administrador': 
        return redirect(url_for('login'))
    
    try:
        nombre = request.form.get('nombre', 'Producto').strip()
        categoria_id = int(request.form.get('categoria', 1))
        es_preparado = int(request.form.get('es_preparado', 0))
        es_pack = 1 if request.form.get('es_pack') == '1' else 0
        codigo_barras = (request.form.get('codigo_barras') or '').strip() or None
        precio_unidad_venta = request.form.get('precio_unidad_venta')
        precio_medio_venta = request.form.get('precio_medio_venta')

        try:
            precio_unidad_venta = float(precio_unidad_venta) if precio_unidad_venta not in (None, '') else None
        except (TypeError, ValueError):
            precio_unidad_venta = None

        try:
            precio_medio_venta = float(precio_medio_venta) if precio_medio_venta not in (None, '') else None
        except (TypeError, ValueError):
            precio_medio_venta = None
        
        # 🔑 LÓGICA CRUCIAL: Determinar precio y stock según tipo de producto
        if es_pack:
            # Es por PACKS
            precio = float(request.form.get('precio_pack') or 0)
            stock_pacas = float(request.form.get('stock_pacas') or 0)
            unidades_por_pack = float(request.form.get('unidades_por_pack') or 1)
            stock = stock_pacas * unidades_por_pack
        else:
            # Es STOCK SIMPLE
            precio = float(request.form.get('precio') or 0)
            stock = 0.0 if es_preparado == 1 else float(request.form.get('stock') or 0)
            unidades_por_pack = 1
        
        db = conectar_db()
        try:
            db.execute("BEGIN TRANSACTION;")
            
            db.execute("""
                UPDATE productos 
                SET nombre = ?, precio = ?, cantidad_stock = ?, categoria_id = ?, 
                    es_preparado = ?, es_pack = ?, unidades_por_pack = ?, codigo_barras = ?,
                    precio_unidad_venta = ?, precio_medio_venta = ?
                WHERE id = ?
            """, (
                nombre,
                precio,
                stock,
                categoria_id,
                es_preparado,
                es_pack,
                unidades_por_pack,
                codigo_barras,
                precio_unidad_venta,
                precio_medio_venta,
                id_producto
            ))
            
            db.execute("DELETE FROM recetas WHERE producto_id = ?", (id_producto,))
            
            if es_preparado == 1:
                insumos_seleccionados = request.form.getlist('insumos_receta[]')
                cantidades_receta = request.form.getlist('cantidades_receta[]')
                
                receta_mapeada = {}
                for ins_id, cant in zip(insumos_seleccionados, cantidades_receta):
                    if ins_id and cant:
                        try:
                            id_i = int(ins_id)
                            cant_f = float(cant)
                            receta_mapeada[id_i] = receta_mapeada.get(id_i, 0.0) + cant_f
                        except ValueError:
                            continue
                
                for ins_id, total_cant in receta_mapeada.items():
                    db.execute("INSERT INTO recetas (producto_id, insumo_id, cantidad_gastada) VALUES (?, ?, ?)",
                               (id_producto, ins_id, total_cant))
            
            db.commit()
            flash(f"✅ Producto '{nombre}' actualizado correctamente", "success")
        except Exception as e:
            db.rollback()
            print(f"ERROR: Error al actualizar producto: {e}")
            flash(f"Error al actualizar: {str(e)}", "error")
        finally:
            db.close()
            
    except ValueError as e:
        print(f"ERROR: Error de conversion: {e}")
        flash("Error: Verifique que los números sean válidos", "error")
    except Exception as e:
        print(f"ERROR: Error general: {e}")
        flash(f"Error inesperado: {str(e)}", "error")
        
    return redirect(url_for('panel_administrador'))

@app.route('/admin/editar_categoria/<int:id>', methods=['POST'])
def editar_categoria(id):
    if 'usuario' not in session or session['rol'] != 'administrador':
        return redirect(url_for('login'))
    
    nuevo_nombre = request.form.get('nuevo_nombre', '').strip()
    nuevo_icono = request.form.get('icono_categoria', '').strip() or None
    
    if not nuevo_nombre:
        flash("El nombre de la categoría no puede estar vacío", "error")
        return redirect(url_for('panel_administrador'))
    
    db = conectar_db()
    try:
        # Asegúrate de que el nombre correcto de la columna sea 'nombre_categoria'
        db.execute("UPDATE categorias SET nombre_categoria = ?, icono = ? WHERE id = ?", (nuevo_nombre, nuevo_icono, id))
        db.commit()
        flash(f"✅ Categoría actualizada a '{nuevo_nombre}'", "success")
    except Exception as e:
        print(f"Error al actualizar categoria: {e}")
        flash("Error al actualizar la categoría", "error")
    finally:
        db.close()
    
    return redirect(url_for('panel_administrador'))

###
@app.route('/admin/eliminar_categoria/<int:id>', methods=['POST'])
def eliminar_categoria(id):
    if 'usuario' not in session or session['rol'] != 'administrador':
        return redirect(url_for('login'))
        
    db = conectar_db()
    try:
        # Verificamos si hay productos en esta categoría
        cursor = db.execute("SELECT COUNT(*) FROM productos WHERE categoria_id = ?", (id,))
        cantidad = cursor.fetchone()[0]
        
        if cantidad > 0:
            flash('No se puede eliminar: hay productos asociados a esta categoría.', 'error')
        else:
            db.execute("DELETE FROM categorias WHERE id = ?", (id,))
            db.commit()
            flash('✅ Categoría eliminada con éxito', 'success')
    except Exception as e:
        flash(f'Error al eliminar: {e}', 'error')
    finally:
        db.close()
    return redirect(url_for('panel_administrador'))

@app.route('/api/receta/<int:producto_id>')
def api_receta_producto(producto_id):
    if 'usuario' not in session:
        return jsonify({'error': 'No autorizado'}), 401
    db = conectar_db()
    receta = db.execute("""
        SELECT insumo_id, cantidad_gastada
        FROM recetas
        WHERE producto_id = ?
    """, (producto_id,)).fetchall()
    db.close()
    return jsonify([{'insumo_id': r['insumo_id'], 'cantidad_gastada': r['cantidad_gastada']} for r in receta])


# ==========================================
# 8. APIS (JSON) PARA PANTALLA DE VENTAS
# ==========================================
@app.route('/api/productos')
def api_productos():
    db = conectar_db()
    # La consulta SQL es correcta, solo aseguramos que el alias sea 'categoria_nombre'
    query = """
        SELECT p.id, p.nombre, p.precio, p.cantidad_stock, p.es_preparado, 
               p.es_pack, p.unidades_por_pack, p.categoria_id, p.codigo_barras,
               p.precio_unidad_venta, p.precio_medio_venta,
               c.nombre_categoria as categoria_nombre
        FROM productos p
        LEFT JOIN categorias c ON p.categoria_id = c.id
    """
    productos = db.execute(query).fetchall()
    db.close()
    
    lista_productos = []
    for p in productos:
        d = dict(p)
        try:
            d['categoria_id'] = int(p['categoria_id']) if (p['categoria_id'] is not None and str(p['categoria_id']).strip() != '') else None
        except (ValueError, TypeError):
            d['categoria_id'] = None
        lista_productos.append(d)
        
    resp = jsonify(lista_productos)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route('/api/resumen_turno_actual')
def api_resumen_turno_actual():
    if 'usuario' not in session:
        return jsonify({'error': 'No autorizado', 'success': False}), 401

    usuario_id = session.get('id')
    turno_id = session.get('turno_id') or obtener_turno_activo(usuario_id)
    db = conectar_db()
    try:
        saldos_cuentas_actuales = db.execute("""
            WITH movimientos AS (
                SELECT COALESCE(metodo_pago, 'efectivo') as metodo_pago, SUM(total) as entradas, 0 as salidas
                FROM ventas
                WHERE COALESCE(metodo_pago, 'efectivo') != 'fiado'
                GROUP BY COALESCE(metodo_pago, 'efectivo')
                UNION ALL
                SELECT COALESCE(metodo_pago, 'efectivo') as metodo_pago, SUM(monto) as entradas, 0 as salidas
                FROM abonos_deuda
                GROUP BY COALESCE(metodo_pago, 'efectivo')
                UNION ALL
                SELECT COALESCE(metodo_pago, 'efectivo') as metodo_pago, 0 as entradas, SUM(monto) as salidas
                FROM gastos
                GROUP BY COALESCE(metodo_pago, 'efectivo')
            )
            SELECT
                metodo_pago,
                SUM(entradas) as entradas,
                SUM(salidas) as salidas,
                (SUM(entradas) - SUM(salidas)) as saldo
            FROM movimientos
            GROUP BY metodo_pago
            ORDER BY metodo_pago ASC
        """).fetchall()

        caja_efectivo_actual = db.execute("""
            SELECT
                COALESCE((
                    SELECT SUM(total)
                    FROM ventas
                    WHERE COALESCE(metodo_pago, 'efectivo') = 'efectivo'
                ), 0)
                +
                COALESCE((
                    SELECT SUM(monto)
                    FROM abonos_deuda
                    WHERE COALESCE(metodo_pago, 'efectivo') = 'efectivo'
                ), 0)
                -
                COALESCE((
                    SELECT SUM(monto)
                    FROM gastos
                    WHERE COALESCE(metodo_pago, 'efectivo') = 'efectivo'
                ), 0) AS caja_efectivo
        """).fetchone()

        deuda_fiado_actual = db.execute("""
            SELECT COALESCE(SUM(saldo_pendiente), 0) as total_deuda
            FROM deudas_clientes
            WHERE estado = 'pendiente'
        """).fetchone()

        if not turno_id:
            return jsonify({
                'success': True,
                'turno_id': None,
                'total_ventas': 0,
                'cantidad_ventas': 0,
                'total_gastos': 0,
                'saldo_turno': 0,
                'movimientos': 0,
                'caja_efectivo_actual': float((caja_efectivo_actual['caja_efectivo'] if caja_efectivo_actual else 0) or 0),
                'deuda_fiado_actual': float((deuda_fiado_actual['total_deuda'] if deuda_fiado_actual else 0) or 0),
                'saldos_cuentas_actuales': [dict(x) for x in saldos_cuentas_actuales],
                'ventas_por_metodo': [],
                'productos_vendidos': []
            })

        resumen_ventas = db.execute("""
            SELECT
                COALESCE(SUM(total), 0) as total_ventas,
                COUNT(id) as cantidad_ventas
            FROM ventas
            WHERE turno_id = ? AND vendedor_id = ?
        """, (turno_id, usuario_id)).fetchone()

        resumen_gastos = db.execute("""
            SELECT COALESCE(SUM(monto), 0) as total_gastos
            FROM gastos
            WHERE turno_id = ? AND vendedor_id = ?
        """, (turno_id, usuario_id)).fetchone()

        movimientos = db.execute("""
            SELECT COUNT(id) as total_movimientos
            FROM caja_movimientos
            WHERE turno_id = ? AND usuario_id = ?
        """, (turno_id, usuario_id)).fetchone()

        ventas_por_metodo = db.execute("""
            SELECT
                metodo_pago,
                COUNT(id) as cantidad,
                COALESCE(SUM(total), 0) as total
            FROM ventas
            WHERE turno_id = ? AND vendedor_id = ?
            GROUP BY metodo_pago
            ORDER BY total DESC
        """, (turno_id, usuario_id)).fetchall()

        productos_vendidos = db.execute("""
            SELECT
                p.nombre as producto,
                COALESCE(SUM(dv.cantidad), 0) as cantidad,
                COALESCE(SUM(dv.cantidad * dv.precio_unitario), 0) as total
            FROM detalle_ventas dv
            JOIN ventas v ON v.id = dv.venta_id
            JOIN productos p ON p.id = dv.producto_id
            WHERE v.turno_id = ? AND v.vendedor_id = ?
            GROUP BY p.id, p.nombre
            ORDER BY cantidad DESC, total DESC
            LIMIT 12
        """, (turno_id, usuario_id)).fetchall()

        total_ventas = float(resumen_ventas['total_ventas'] or 0)
        total_gastos = float(resumen_gastos['total_gastos'] or 0)
        saldo_turno = total_ventas - total_gastos

        return jsonify({
            'success': True,
            'turno_id': turno_id,
            'total_ventas': total_ventas,
            'cantidad_ventas': int(resumen_ventas['cantidad_ventas'] or 0),
            'total_gastos': total_gastos,
            'saldo_turno': saldo_turno,
            'movimientos': int(movimientos['total_movimientos'] or 0),
            'caja_efectivo_actual': float((caja_efectivo_actual['caja_efectivo'] if caja_efectivo_actual else 0) or 0),
            'deuda_fiado_actual': float((deuda_fiado_actual['total_deuda'] if deuda_fiado_actual else 0) or 0),
            'saldos_cuentas_actuales': [dict(x) for x in saldos_cuentas_actuales],
            'ventas_por_metodo': [dict(x) for x in ventas_por_metodo],
            'productos_vendidos': [dict(x) for x in productos_vendidos]
        })
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500
    finally:
        db.close()


@app.route('/api/deudas_pendientes')
def api_deudas_pendientes():
    if 'usuario' not in session:
        return jsonify({'error': 'No autorizado', 'success': False}), 401

    db = conectar_db()
    try:
        deudas = db.execute("""
            SELECT
                cliente_nombre,
                COUNT(id) as ventas_fiadas,
                COALESCE(SUM(saldo_pendiente), 0) as saldo_pendiente
            FROM deudas_clientes
            WHERE estado = 'pendiente'
            GROUP BY cliente_nombre
            HAVING COALESCE(SUM(saldo_pendiente), 0) > 0
            ORDER BY saldo_pendiente DESC, cliente_nombre ASC
        """).fetchall()

        return jsonify({'success': True, 'deudas': [dict(d) for d in deudas]})
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500
    finally:
        db.close()


@app.route('/api/registrar_abono_fiado', methods=['POST'])
def api_registrar_abono_fiado():
    if 'usuario' not in session:
        return jsonify({'error': 'No autorizado', 'success': False}), 401

    data = request.json or {}
    cliente_nombre = (data.get('cliente_nombre') or '').strip()
    metodo_pago = (data.get('metodo_pago') or 'efectivo').strip().lower()
    referencia = (data.get('referencia') or '').strip()
    vendedor_id = session.get('id')
    turno_id = session.get('turno_id') or obtener_turno_activo(vendedor_id)

    try:
        monto_abono = float(data.get('monto', 0) or 0)
    except (TypeError, ValueError):
        return jsonify({'error': 'Monto inválido para el abono', 'success': False}), 400

    if not cliente_nombre:
        return jsonify({'error': 'Debes indicar el cliente del abono', 'success': False}), 400

    if monto_abono <= 0:
        return jsonify({'error': 'El abono debe ser mayor a 0', 'success': False}), 400

    metodos_validos = {'efectivo', 'nequi', 'daviplata', 'tarjeta'}
    if metodo_pago not in metodos_validos:
        metodo_pago = 'efectivo'

    db = conectar_db()
    try:
        saldo_actual_row = db.execute("""
            SELECT COALESCE(SUM(saldo_pendiente), 0) as saldo_total
            FROM deudas_clientes
            WHERE estado = 'pendiente' AND LOWER(cliente_nombre) = LOWER(?)
        """, (cliente_nombre,)).fetchone()

        saldo_actual = float((saldo_actual_row['saldo_total'] if saldo_actual_row else 0) or 0)
        if saldo_actual <= 0:
            return jsonify({'error': 'Este cliente no tiene deuda pendiente', 'success': False}), 400

        if monto_abono - saldo_actual > 0.0001:
            return jsonify({'error': 'El abono supera la deuda pendiente del cliente', 'success': False}), 400

        db.execute("BEGIN TRANSACTION;")

        db.execute("""
            INSERT INTO abonos_deuda (cliente_nombre, monto, metodo_pago, referencia, vendedor_id, turno_id, fecha)
            VALUES (?, ?, ?, ?, ?, ?, DATETIME('now', 'localtime'))
        """, (cliente_nombre, monto_abono, metodo_pago, referencia, vendedor_id, turno_id))

        deudas = db.execute("""
            SELECT id, venta_id, monto_pagado, saldo_pendiente
            FROM deudas_clientes
            WHERE estado = 'pendiente' AND LOWER(cliente_nombre) = LOWER(?)
            ORDER BY fecha_creacion ASC, id ASC
        """, (cliente_nombre,)).fetchall()

        restante = monto_abono
        for deuda in deudas:
            if restante <= 0:
                break

            saldo_deuda = float(deuda['saldo_pendiente'] or 0)
            if saldo_deuda <= 0:
                continue

            aplicar = min(restante, saldo_deuda)
            nuevo_pagado = float(deuda['monto_pagado'] or 0) + aplicar
            nuevo_saldo = saldo_deuda - aplicar
            deuda_pagada = nuevo_saldo <= 0.0001

            db.execute("""
                UPDATE deudas_clientes
                SET monto_pagado = ?,
                    saldo_pendiente = ?,
                    estado = ?,
                    fecha_actualizacion = DATETIME('now', 'localtime')
                WHERE id = ?
            """, (nuevo_pagado, max(0, nuevo_saldo), ('pagada' if deuda_pagada else 'pendiente'), deuda['id']))

            db.execute("""
                UPDATE ventas
                SET estado_cobro = ?
                WHERE id = ?
            """, (('pagado' if deuda_pagada else 'parcial'), deuda['venta_id']))

            restante -= aplicar

        registrar_movimiento_caja(
            db,
            turno_id,
            vendedor_id,
            'entrada',
            'abono_fiado',
            f'Abono deuda cliente: {cliente_nombre}',
            metodo_pago,
            monto_abono
        )

        db.commit()

        saldo_restante = max(0, saldo_actual - monto_abono)
        return jsonify({
            'success': True,
            'cliente_nombre': cliente_nombre,
            'abono_registrado': monto_abono,
            'saldo_restante': saldo_restante
        })
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e), 'success': False}), 500
    finally:
        db.close()


@app.route('/api/movimientos_pago_recientes')
def api_movimientos_pago_recientes():
    if 'usuario' not in session:
        return jsonify({'error': 'No autorizado', 'success': False}), 401

    db = conectar_db()
    try:
        turnos_recientes = db.execute("""
            SELECT id, fecha_apertura, estado, COALESCE(nombre_usuario, 'Cajero') as nombre_usuario
            FROM turnos
            ORDER BY id DESC
            LIMIT 7
        """).fetchall()

        list_turnos = []
        for t in turnos_recientes:
            estado_lbl = '(Activo)' if t['estado'] == 'abierto' else '(Cerrado)'
            list_turnos.append({
                'id': t['id'],
                'etiqueta': f"Turno #{int(t['id']):04d} - {t['nombre_usuario']} ({t['fecha_apertura']}) {estado_lbl}"
            })

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

        return jsonify({'success': True, 'turnos': list_turnos, 'movimientos': movimientos})
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500
    finally:
        db.close()


@app.route('/api/corregir_metodo_pago', methods=['POST'])
def api_corregir_metodo_pago():
    if 'usuario' not in session:
        return jsonify({'error': 'No autorizado', 'success': False}), 401

    data = request.json or {}
    tipo = (data.get('tipo') or '').strip().lower()
    nuevo_metodo = (data.get('nuevo_metodo') or '').strip().lower()

    try:
        registro_id = int(data.get('registro_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'ID de registro inválido', 'success': False}), 400

    metodos_validos = {'efectivo', 'nequi', 'daviplata', 'tarjeta'}
    if nuevo_metodo not in metodos_validos:
        return jsonify({'error': 'Método de pago inválido para corrección', 'success': False}), 400

    db = conectar_db()
    try:
        db.execute("BEGIN TRANSACTION;")

        if tipo == 'venta':
            registro = db.execute("SELECT id, COALESCE(metodo_pago, 'efectivo') as metodo_pago FROM ventas WHERE id = ?", (registro_id,)).fetchone()
            if not registro:
                return jsonify({'error': 'Venta no encontrada', 'success': False}), 404
            if str(registro['metodo_pago']).lower() == 'fiado':
                return jsonify({'error': 'No se puede corregir por aquí una venta fiada', 'success': False}), 400

            db.execute("UPDATE ventas SET metodo_pago = ? WHERE id = ?", (nuevo_metodo, registro_id))

        elif tipo == 'gasto':
            registro = db.execute("SELECT id FROM gastos WHERE id = ?", (registro_id,)).fetchone()
            if not registro:
                return jsonify({'error': 'Gasto no encontrado', 'success': False}), 404

            db.execute("UPDATE gastos SET metodo_pago = ? WHERE id = ?", (nuevo_metodo, registro_id))

        elif tipo == 'abono':
            registro = db.execute("SELECT id FROM abonos_deuda WHERE id = ?", (registro_id,)).fetchone()
            if not registro:
                return jsonify({'error': 'Abono no encontrado', 'success': False}), 404

            db.execute("UPDATE abonos_deuda SET metodo_pago = ? WHERE id = ?", (nuevo_metodo, registro_id))

        else:
            return jsonify({'error': 'Tipo de registro inválido', 'success': False}), 400

        db.commit()
        return jsonify({'success': True, 'registro_id': registro_id, 'tipo': tipo, 'nuevo_metodo': nuevo_metodo})
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e), 'success': False}), 500
    finally:
        db.close()

@app.route('/admin/add_gasto', methods=['POST'])
def add_gasto():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    destino = 'panel_administrador' if session.get('rol') == 'administrador' else 'pantalla_ventas'
    
    try:
        tipo_gasto = request.form.get('tipo_gasto', 'general')
        monto = float(request.form.get('monto', 0))
        descripcion = request.form.get('descripcion', 'Sin descripción')
        categoria_gasto = request.form.get('categoria_gasto', 'Otros Gastos')
        metodo_pago = (request.form.get('metodo_pago', 'efectivo') or 'efectivo').strip().lower()
        vendedor_id = session.get('id')
        turno_id = session.get('turno_id') or obtener_turno_activo(vendedor_id)
        metodos_validos = {'efectivo', 'nequi', 'daviplata', 'tarjeta'}
        if metodo_pago not in metodos_validos:
            metodo_pago = 'efectivo'
        
        if monto <= 0:
            flash("El monto debe ser mayor a 0", "error")
            return redirect(url_for(destino))
        
        db = conectar_db()
        cursor = db.cursor()
        
        try:
            descripcion_usuario = (request.form.get('descripcion') or '').strip()
            
            # Construir la descripción y categoría explícita según el tipo de gasto
            if tipo_gasto == 'insumo':
                insumo_id = request.form.get('insumo_id')
                cantidad_comprada = request.form.get('cantidad_comprada')
                categoria_gasto = 'Compra Insumo'
                nom_insumo = 'Insumo'
                if insumo_id:
                    row_i = cursor.execute("SELECT nombre_insumo, unidad_medida FROM insumos WHERE id = ?", (insumo_id,)).fetchone()
                    if row_i:
                        nom_insumo = f"{row_i['nombre_insumo']} (x{cantidad_comprada or 1} {row_i['unidad_medida']})"
                
                descripcion = f"Compra Insumo: {nom_insumo}"
                if descripcion_usuario and descripcion_usuario != 'Sin descripción':
                    descripcion += f" - {descripcion_usuario}"

            elif tipo_gasto == 'producto':
                producto_id = request.form.get('producto_id')
                cantidad_producto = request.form.get('cantidad_producto_comprada')
                categoria_gasto = 'Compra Inventario'
                nom_prod = 'Producto'
                if producto_id:
                    row_p = cursor.execute("SELECT nombre, es_pack FROM productos WHERE id = ?", (producto_id,)).fetchone()
                    if row_p:
                        pack_lbl = 'pacas' if row_p['es_pack'] else 'uds'
                        nom_prod = f"{row_p['nombre']} (x{cantidad_producto or 1} {pack_lbl})"
                
                descripcion = f"Compra Inventario: {nom_prod}"
                if descripcion_usuario and descripcion_usuario != 'Sin descripción':
                    descripcion += f" - {descripcion_usuario}"
            else:
                categoria_gasto = 'Gasto General'
                descripcion = descripcion_usuario or 'Gasto General'

            # Registrar gasto
            cursor.execute("""
                INSERT INTO gastos (vendedor_id, turno_id, descripcion, monto, categoria_gasto, metodo_pago, fecha) 
                VALUES (?, ?, ?, ?, ?, ?, DATETIME('now', 'localtime'))
            """, (vendedor_id, turno_id, descripcion, monto, categoria_gasto, metodo_pago))

            registrar_movimiento_caja(
                db,
                turno_id,
                vendedor_id,
                'salida',
                'gasto',
                descripcion,
                metodo_pago,
                monto
            )
            
            # Actualizar stock de insumos
            if tipo_gasto == 'insumo' and insumo_id and cantidad_comprada:
                try:
                    cursor.execute("""
                        UPDATE insumos 
                        SET cantidad_actual = cantidad_actual + ? 
                        WHERE id = ?
                    """, (float(cantidad_comprada), int(insumo_id)))
                except ValueError:
                    pass

            # Actualizar stock de productos
            elif tipo_gasto == 'producto' and producto_id and cantidad_producto:
                try:
                    producto = cursor.execute("""
                        SELECT es_pack, unidades_por_pack
                        FROM productos
                        WHERE id = ?
                    """, (int(producto_id),)).fetchone()

                    if producto and float(cantidad_producto) > 0:
                        es_pack = 1 if producto['es_pack'] else 0
                        unidades_por_pack = float(producto['unidades_por_pack'] or 1)
                        cantidad_a_sumar = (float(cantidad_producto) * unidades_por_pack) if es_pack else float(cantidad_producto)

                        cursor.execute("""
                            UPDATE productos
                            SET cantidad_stock = cantidad_stock + ?
                            WHERE id = ?
                        """, (cantidad_a_sumar, int(producto_id)))
                except ValueError:
                    pass
            
            db.commit()
            flash(f"✅ Gasto registrado: ${monto:,.0f}", "success")
        except Exception as e:
            db.rollback()
            print(f"Error al registrar gasto: {e}")
            flash(f"Error al registrar gasto: {str(e)}", "error")
        finally:
            db.close()
    
    except ValueError as e:
        print(f"Error de conversion: {e}")
        flash("Error: Verifique que los números sean válidos", "error")
    except Exception as e:
        print(f"Error general: {e}")
        flash(f"Error inesperado: {str(e)}", "error")

    return redirect(url_for(destino))

@app.route('/admin/registrar_compra', methods=['POST'])
def registrar_compra():
    if 'usuario' not in session: 
        return redirect(url_for('login'))
    
    # Obtenemos datos del formulario
    tipo = request.form.get('tipo_gasto') # Valores: 'gasto_fijo', 'insumo' o 'producto'
    monto = float(request.form.get('monto', 0))
    descripcion = request.form.get('descripcion', 'Sin descripción')
    
    # Datos para el inventario
    prod_id = request.form.get('producto_id')
    cantidad = float(request.form.get('cantidad', 0))
    
    db = conectar_db()
    cursor = db.cursor()
    
    try:
        # 1. Registrar egreso en la tabla de gastos
        cursor.execute("INSERT INTO gastos (descripcion, monto, fecha) VALUES (?, ?, DATETIME('now'))", 
                       (descripcion, monto))
        
        # 2. Lógica inteligente para actualizar stock según el tipo
        if (tipo == 'insumo' or tipo == 'producto') and prod_id and cantidad > 0:
            tabla = 'insumos' if tipo == 'insumo' else 'productos'
            columna = 'cantidad_actual' if tipo == 'insumo' else 'cantidad_stock'
            cursor.execute(f"UPDATE {tabla} SET {columna} = {columna} + ? WHERE id = ?", 
                           (amount := cantidad, prod_id))
        
        db.commit()
        flash('Operación completada: Gasto registrado y stock actualizado.', 'success')
    except Exception as e:
        db.rollback()
        print(f"Error al procesar la compra: {e}")
        flash('Error al procesar la compra', 'error')
    finally:
        db.close()
    
    return redirect(url_for('pantalla_ventas'))

@app.route('/admin/add_insumo', methods=['POST'])
def add_insumo():
    if 'usuario' not in session or session['rol'] != 'administrador':
        return redirect(url_for('login'))
        
    nombre = request.form.get('nombre_insumo', '').strip()
    cantidad = request.form.get('cantidad_actual', '')
    unidad = request.form.get('unidad_medida', 'Unid')
    
    if nombre and cantidad:
        db = conectar_db()
        try:
            cantidad_float = float(cantidad)
            db.execute("INSERT INTO insumos (nombre_insumo, cantidad_actual, unidad_medida) VALUES (?, ?, ?)", 
                       (nombre, cantidad_float, unidad))
            db.commit()
            flash('✅ Insumo agregado con éxito', 'success')
        except ValueError:
            flash('Error: La cantidad debe ser un número válido', 'error')
        except Exception as e:
            print(f"Error al guardar insumo: {e}")
            flash(f'Error: {str(e)}', 'error')
        finally:
            db.close()
    else:
        flash('Error: Nombre y cantidad son requeridos', 'error')
            
    return redirect(url_for('panel_administrador'))

@app.route('/api/cobrar', methods=['POST'])
def api_cobrar():
    if 'usuario' not in session:
        return jsonify({'error': 'No autorizado', 'success': False}), 401
    
    data = request.json
    carrito = data.get('carrito', [])
    metodo_pago = (data.get('metodo_pago', 'efectivo') or 'efectivo').strip().lower()
    efectivo_recibido = data.get('efectivo_recibido', 0)
    referencia_pago = data.get('referencia_pago', '')
    cliente_fiado = (data.get('cliente_fiado', '') or '').strip()
    vendedor_id = session.get('id')
    turno_id = session.get('turno_id') or obtener_turno_activo(vendedor_id)
    metodos_validos = {'efectivo', 'nequi', 'daviplata', 'tarjeta', 'fiado'}
    if metodo_pago not in metodos_validos:
        metodo_pago = 'efectivo'
    
    if not carrito:
        return jsonify({'error': 'Carrito vacío', 'success': False}), 400
    
    db = conectar_db()
    try:
        total_venta = sum(item['precio'] * item['cantidad'] for item in carrito)

        if metodo_pago == 'fiado':
            if not cliente_fiado:
                return jsonify({'error': 'Debes indicar el cliente exclusivo para registrar el fiado', 'success': False}), 400

            cliente_existe = db.execute("""
                SELECT id, nombre
                FROM clientes_exclusivos
                WHERE LOWER(nombre) = LOWER(?) AND activo = 1
                LIMIT 1
            """, (cliente_fiado,)).fetchone()

            if not cliente_existe:
                return jsonify({'error': 'El cliente no está autorizado para compras fiadas', 'success': False}), 400

            cliente_fiado = cliente_existe['nombre']
        
        # Calcular efectivo recibido y cambio para evitar IntegrityError en campos NOT NULL
        if metodo_pago == 'fiado':
            efectivo_recibido_val = 0.0
            cambio_val = 0.0
        elif metodo_pago != 'efectivo':
            efectivo_recibido_val = total_venta
            cambio_val = 0.0
        else:
            try:
                efectivo_recibido_val = float(efectivo_recibido)
            except (ValueError, TypeError):
                efectivo_recibido_val = total_venta
            cambio_val = max(0.0, efectivo_recibido_val - total_venta)

        # Insertar venta
        cursor_venta = db.execute("""
            INSERT INTO ventas (vendedor_id, turno_id, metodo_pago, total, efectivo_recibido, cambio, referencia_pago, cliente_fiado, estado_cobro, tipo_venta, fecha)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'producto', datetime('now', 'localtime'))
        """, (
            vendedor_id,
            turno_id,
            metodo_pago,
            total_venta,
            efectivo_recibido_val,
            cambio_val,
            referencia_pago,
            (cliente_fiado if metodo_pago == 'fiado' else None),
            ('pendiente' if metodo_pago == 'fiado' else 'pagado')
        ))
        
        venta_id = cursor_venta.lastrowid

        tipo_movimiento_caja = 'entrada' if metodo_pago != 'fiado' else 'deuda'
        registrar_movimiento_caja(
            db,
            turno_id,
            vendedor_id,
            tipo_movimiento_caja,
            'venta',
            f'Venta #{venta_id}',
            metodo_pago,
            total_venta
        )

        if metodo_pago == 'fiado':
            db.execute("""
                INSERT INTO deudas_clientes (
                    venta_id,
                    cliente_nombre,
                    monto_total,
                    monto_pagado,
                    saldo_pendiente,
                    estado,
                    fecha_creacion,
                    fecha_actualizacion
                )
                VALUES (?, ?, ?, 0, ?, 'pendiente', DATETIME('now', 'localtime'), DATETIME('now', 'localtime'))
            """, (venta_id, cliente_fiado, total_venta, total_venta))
        
        # Insertar detalles de venta y actualizar stock
        for item in carrito:
            db.execute("""
                INSERT INTO detalle_ventas (venta_id, producto_id, cantidad, precio_unitario)
                VALUES (?, ?, ?, ?)
            """, (venta_id, item['id'], item['cantidad'], item['precio']))
            
            # Actualizar stock (solo si no es preparado)
            if item.get('es_preparado') != 1:
                factor = item.get('factor_descuento', 1)
                cantidad_a_descontar = item['cantidad'] * factor
                db.execute("""
                    UPDATE productos 
                    SET cantidad_stock = cantidad_stock - ?
                    WHERE id = ?
                """, (cantidad_a_descontar, item['id']))
            else:
                # Si es preparado, descontar insumos según la receta
                receta = db.execute("SELECT insumo_id, cantidad_gastada FROM recetas WHERE producto_id = ?", (item['id'],)).fetchall()
                for ing in receta:
                    db.execute("""
                        UPDATE insumos
                        SET cantidad_actual = cantidad_actual - ?
                        WHERE id = ?
                    """, (item['cantidad'] * ing['cantidad_gastada'], ing['insumo_id']))
        
        orden_abierta_id = data.get('orden_abierta_id')
        if orden_abierta_id:
            db.execute("UPDATE ordenes_abiertas SET estado = 'pagada' WHERE id = ?", (orden_abierta_id,))

        db.commit()
        return jsonify({'success': True, 'venta_id': venta_id})
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e), 'success': False}), 500
    finally:
        db.close()


def abrir_cajon_monedero():
    try:
        import win32print
        printer_name = win32print.GetDefaultPrinter()
        hPrinter = win32print.OpenPrinter(printer_name)
        try:
            hJob = win32print.StartDocPrinter(hPrinter, 1, ("Abrir Cajon", None, "RAW"))
            try:
                win32print.StartPagePrinter(hPrinter)
                # Comando ESC/POS para abrir cajón en pin 2 y pin 5 para máxima compatibilidad
                win32print.WritePrinter(hPrinter, b"\x1b\x70\x00\x19\xfa")
                win32print.WritePrinter(hPrinter, b"\x1b\x70\x01\x19\xfa")
                win32print.EndPagePrinter(hPrinter)
            finally:
                win32print.EndDocPrinter(hPrinter)
        finally:
            win32print.ClosePrinter(hPrinter)
        return True, None
    except Exception as e:
        return False, str(e)


@app.route('/api/abrir_cajon', methods=['POST'])
def api_abrir_cajon():
    if 'usuario' not in session:
        return jsonify({'error': 'No autorizado', 'success': False}), 401
    
    exito, error = abrir_cajon_monedero()
    if exito:
        return jsonify({'success': True, 'message': 'Se envió señal de apertura al cajón monedero.'})
    else:
        return jsonify({'success': False, 'error': f"No se pudo abrir el cajón: {error}"}), 500


def imprimir_factura_directa(venta_id):
    try:
        import sqlite3
        db = conectar_db()
        
        # 1. Obtener la venta
        venta = db.execute("""
            SELECT v.*, u.nombre as vendedor_nombre
            FROM ventas v
            LEFT JOIN usuarios u ON v.vendedor_id = u.id
            WHERE v.id = ?
        """, (venta_id,)).fetchone()
        
        if not venta:
            db.close()
            return False, "Venta no encontrada"
        
        # 2. Obtener los detalles de la venta
        detalles = db.execute("""
            SELECT dv.cantidad, dv.precio_unitario, (dv.cantidad * dv.precio_unitario) as subtotal, p.nombre as producto_nombre
            FROM detalle_ventas dv
            JOIN productos p ON dv.producto_id = p.id
            WHERE dv.venta_id = ?
        """, (venta_id,)).fetchall()
        db.close()
        
        import win32print
        printer_name = win32print.GetDefaultPrinter()
        hPrinter = win32print.OpenPrinter(printer_name)
        try:
            hJob = win32print.StartDocPrinter(hPrinter, 1, (f"Factura_{venta_id}", None, "RAW"))
            try:
                win32print.StartPagePrinter(hPrinter)
                
                # ESC/POS Comandos Básicos:
                ESC = b"\x1b"
                GS = b"\x1d"
                
                # Inicializar impresora
                init = ESC + b"@"
                
                # Modos de texto
                centrado = ESC + b"a\x01" # Centrar texto
                izquierda = ESC + b"a\x00" # Alinear izquierda
                derecha = ESC + b"a\x02" # Alinear derecha
                negrita_on = ESC + b"E\x01"
                negrita_off = ESC + b"E\x00"
                double_size = GS + b"!\x11" # Doble ancho y alto
                normal_size = GS + b"!\x00"
                
                # Corte de papel
                cortar = GS + b"V\x41\x00" # Corte parcial
                
                # Pulso cajon monedero (abrir)
                abrir_cajon = ESC + b"p\x00\x19\xfa" + ESC + b"p\x01\x19\xfa"
                
                raw_data = bytearray()
                raw_data.extend(init)
                
                # Si el pago es en efectivo, abrir cajón
                metodo = (venta['metodo_pago'] or 'efectivo').strip().lower()
                if metodo == 'efectivo':
                    raw_data.extend(abrir_cajon)
                
                # Encabezado (ASCII Coffee Cup Logo + Titulo)
                raw_data.extend(centrado + normal_size + b"  (  )\n   ) (\n .---.\n |   |'-.\n |   |  |\n  \\___/'-'\n\n")
                raw_data.extend(negrita_on + double_size + b"CAFETO 24\n" + normal_size + negrita_off)
                raw_data.extend(b"NIT: 1013587664-8\n")
                raw_data.extend(b"Diagonal 62 sur #22-04, Bogota\n")
                raw_data.extend(b"Tel.: 3015020637\n")
                raw_data.extend(b"alej_z@hotmail.com\n")
                raw_data.extend(b"--------------------------------\n") # 32 caracteres
                
                # Datos del ticket
                raw_data.extend(izquierda)
                ticket_num = f"261-1-{venta['id']:06d}"
                raw_data.extend(f"Ticket: {ticket_num}\n".encode('latin1', errors='replace'))
                raw_data.extend(f"Fecha: {venta['fecha']}\n".encode('latin1', errors='replace'))
                
                vendedor = venta['vendedor_nombre'] or 'Cajero'
                raw_data.extend(f"Atendido: {vendedor}\n".encode('latin1', errors='replace'))
                
                cliente = venta['cliente_fiado']
                if cliente:
                    raw_data.extend(f"Cliente: {cliente}\n".encode('latin1', errors='replace'))
                
                raw_data.extend(b"--------------------------------\n")
                raw_data.extend(negrita_on + b"Cant Producto           Subtotal\n" + negrita_off)
                raw_data.extend(b"--------------------------------\n")
                
                # Detalles de productos (ancho total 32 caracteres)
                for item in detalles:
                    cant = f"{item['cantidad']:.0f}"
                    sub = f"${item['subtotal']:.0f}"
                    nombre = item['producto_nombre']
                    
                    # Cortar el nombre
                    if len(nombre) > 16:
                        nombre = nombre[:16]
                    
                    # Formatear la línea: Cant (3 chars) + Nombre (18 chars) + Subtotal (11 chars derecha)
                    linea = f"{cant:<3}{nombre:<18}{sub:>11}\n"
                    raw_data.extend(linea.encode('latin1', errors='replace'))
                    
                raw_data.extend(b"--------------------------------\n")
                
                # Totales
                total_str = f"${venta['total']:.0f}"
                raw_data.extend(negrita_on + double_size + centrado + f"TOTAL: {total_str}\n".encode('latin1', errors='replace') + normal_size + negrita_off + izquierda)
                raw_data.extend(b"--------------------------------\n")
                
                # Método de pago y cambio
                metodo_label = metodo.upper()
                recibido_val = venta['efectivo_recibido'] if (metodo == 'efectivo' and venta['efectivo_recibido']) else venta['total']
                recibido_str = f"${recibido_val:.0f}"
                cambio_val = venta['cambio'] or 0.0
                cambio_str = f"${cambio_val:.0f}"
                
                raw_data.extend(f"Pago: {metodo_label:<15}{recibido_str:>11}\n".encode('latin1', errors='replace'))
                if metodo == 'efectivo' and cambio_val > 0:
                    raw_data.extend(f"Cambio:        {cambio_str:>11}\n".encode('latin1', errors='replace'))
                
                raw_data.extend(b"--------------------------------\n")
                raw_data.extend(centrado + b"Gracias por su compra\n")
                raw_data.extend(b"Tecnologia Cafeto 24\n\n\n\n\n")
                raw_data.extend(cortar)
                
                win32print.WritePrinter(hPrinter, bytes(raw_data))
                win32print.EndPagePrinter(hPrinter)
            finally:
                win32print.EndDocPrinter(hPrinter)
        finally:
            win32print.ClosePrinter(hPrinter)
        return True, None
    except Exception as e:
        return False, str(e)


@app.route('/api/imprimir_directo/<int:venta_id>', methods=['POST'])
def api_imprimir_directo(venta_id):
    if 'usuario' not in session:
        return jsonify({'error': 'No autorizado', 'success': False}), 401
    
    exito, error = imprimir_factura_directa(venta_id)
    if exito:
        return jsonify({'success': True, 'message': 'Factura enviada a la impresora.'})
    else:
        return jsonify({'success': False, 'error': f"No se pudo imprimir: {error}"}), 500


@app.route('/venta/factura/<int:venta_id>')
def ver_factura_venta(venta_id):
    if 'usuario' not in session:
        return redirect(url_for('login'))

    db = conectar_db()
    try:
        venta = db.execute("""
            SELECT
                v.id,
                v.fecha,
                v.total,
                v.metodo_pago,
                v.efectivo_recibido,
                v.cambio,
                v.referencia_pago,
                v.cliente_fiado,
                v.estado_cobro,
                COALESCE(u.nombre, 'Sin cajero') as vendedor_nombre,
                COALESCE(u.rol, 'sin rol') as vendedor_rol
            FROM ventas v
            LEFT JOIN usuarios u ON u.id = v.vendedor_id
            WHERE v.id = ?
        """, (venta_id,)).fetchone()

        if not venta:
            return "Venta no encontrada", 404

        detalles = db.execute("""
            SELECT
                p.nombre as producto_nombre,
                dv.cantidad,
                dv.precio_unitario,
                (dv.cantidad * dv.precio_unitario) as subtotal
            FROM detalle_ventas dv
            JOIN productos p ON p.id = dv.producto_id
            WHERE dv.venta_id = ?
            ORDER BY p.nombre ASC
        """, (venta_id,)).fetchall()

        return render_template(
            'factura.html',
            venta=venta,
            detalles=detalles,
            autoprint=(request.args.get('autoprint') == '1'),
            nombre=session.get('nombre', ''),
            rol=session.get('rol', '')
        )
    finally:
        db.close()


@app.route('/api/registrar_ingreso_intangible', methods=['POST'])
def api_registrar_ingreso_intangible():
    if 'usuario' not in session:
        return jsonify({'error': 'No autorizado', 'success': False}), 401

    data = request.json or {}
    concepto = (data.get('concepto') or '').strip()
    referencia = (data.get('referencia') or '').strip()
    metodo_pago = (data.get('metodo_pago') or 'efectivo').strip().lower()
    vendedor_id = session.get('id')
    turno_id = session.get('turno_id') or obtener_turno_activo(vendedor_id)

    try:
        monto = float(data.get('monto', 0) or 0)
    except (TypeError, ValueError):
        return jsonify({'error': 'Monto inválido para ingreso intangible', 'success': False}), 400

    if not concepto:
        return jsonify({'error': 'Debes indicar el concepto del ingreso', 'success': False}), 400

    if monto <= 0:
        return jsonify({'error': 'El monto debe ser mayor a 0', 'success': False}), 400

    metodos_validos = {'efectivo', 'nequi', 'daviplata', 'tarjeta'}
    if metodo_pago not in metodos_validos:
        metodo_pago = 'efectivo'

    db = conectar_db()
    try:
        db.execute("BEGIN TRANSACTION;")

        referencia_compuesta = concepto if not referencia else f"{concepto} | {referencia}"
        cursor_venta = db.execute("""
            INSERT INTO ventas (
                vendedor_id,
                turno_id,
                metodo_pago,
                total,
                efectivo_recibido,
                cambio,
                referencia_pago,
                cliente_fiado,
                estado_cobro,
                tipo_venta,
                fecha
            )
            VALUES (?, ?, ?, ?, ?, 0, ?, NULL, 'pagado', 'intangible', DATETIME('now', 'localtime'))
        """, (vendedor_id, turno_id, metodo_pago, monto, monto, referencia_compuesta))

        venta_id = cursor_venta.lastrowid

        registrar_movimiento_caja(
            db,
            turno_id,
            vendedor_id,
            'entrada',
            'ingreso_intangible',
            f'Ingreso intangible: {concepto}',
            metodo_pago,
            monto
        )

        db.commit()
        return jsonify({
            'success': True,
            'venta_id': venta_id,
            'concepto': concepto,
            'monto': monto
        })
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e), 'success': False}), 500
    finally:
        db.close()


# ==========================================
# GESTOR DE MIGRACIONES Y CONTROL DE VERSIONES DE BASE DE DATOS
# ==========================================
def inicializar_y_migrar_db():
    db_path = os.path.join(USER_DATA_DIR, "cafeteria.db")
    respaldo_path = None
    
    # 1. Conectar y asegurar la existencia de la tabla db_metadata
    conn = conectar_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS db_metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        conn.commit()
        
        # 2. Obtener versión actual
        row = cursor.execute("SELECT value FROM db_metadata WHERE key = 'version'").fetchone()
        
        if row is None:
            # Si no hay versión registrada, determinar si es una DB nueva o preexistente
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'db_metadata'")
            tables = [r[0] for r in cursor.fetchall()]
            if not tables:
                version_actual = 0  # DB vacía/nueva
            else:
                # DB preexistente. Si tiene la tabla 'turnos', asumimos que es versión 2
                if 'turnos' in tables:
                    version_actual = 2
                else:
                    version_actual = 1
                cursor.execute("INSERT OR REPLACE INTO db_metadata (key, value) VALUES ('version', ?)", (str(version_actual),))
                conn.commit()
        else:
            version_actual = int(row[0])
            
        # ==========================================
        # AUTO-CURACIÓN DE CATEGORÍAS Y PRODUCTOS HUÉRFANOS
        # ==========================================
        try:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='categorias'")
            if cursor.fetchone():
                cursor.execute("SELECT id, nombre_categoria FROM categorias")
                cats = cursor.fetchall()
                sin_cat_id = None
                for cid, name in cats:
                    cnom_lower = name.lower() if isinstance(name, str) else str(name or '').lower()
                    if 'sin categor' in cnom_lower or 'sin categ' in cnom_lower:
                        sin_cat_id = cid
                        if name != 'Sin Categoría':
                            cursor.execute("UPDATE categorias SET nombre_categoria = ? WHERE id = ?", ('Sin Categoría', cid))
                        break
                
                if sin_cat_id is None:
                    cursor.execute("INSERT INTO categorias (nombre_categoria) VALUES (?)", ('Sin Categoría',))
                    sin_cat_id = cursor.lastrowid
                
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='productos'")
                if cursor.fetchone():
                    cursor.execute("""
                        UPDATE productos 
                        SET categoria_id = ?
                        WHERE categoria_id IS NULL OR categoria_id NOT IN (SELECT id FROM categorias)
                    """, (sin_cat_id,))
                
                conn.commit()
        except Exception as heal_err:
            print(f"ERROR en auto-curación de base de datos: {heal_err}")
            
    except Exception as e:
        print(f"ERROR: Error al inicializar metadatos de DB: {e}")
        conn.close()
        return
    finally:
        conn.close()
        
    VERSION_OBJETIVO = 3  # Versión actual de la base de datos del sistema
    
    if version_actual >= VERSION_OBJETIVO:
        print(f"INFO: Base de datos en su versión más reciente (v{version_actual}).")
        return

    print(f"INFO: La base de datos requiere actualización (v{version_actual} -> v{VERSION_OBJETIVO}).")
    
    # 3. Realizar copia de respaldo de seguridad antes de modificar nada
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    respaldo_path = os.path.join(USER_DATA_DIR, f"cafeteria_backup_v{version_actual}_{timestamp}.db")
    try:
        if os.path.exists(db_path):
            shutil.copy2(db_path, respaldo_path)
            print(f"INFO: RESPALDO de seguridad creado exitosamente en: {respaldo_path}")
    except Exception as backup_err:
        print(f"ERROR: No se pudo realizar el respaldo de la base de datos: {backup_err}")
        return  # Detener la migración por seguridad
        
    # 4. Iniciar ejecución secuencial de migraciones
    conn = conectar_db()
    cursor = conn.cursor()
    try:
        # ==========================================
        # MIGRACIÓN v1: Creación del esquema base inicial (si la DB es nueva)
        # ==========================================
        if version_actual < 1:
            print("INFO: Aplicando migración v1 (Esquema base inicial)...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS usuarios (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nombre TEXT NOT NULL,
                    usuario TEXT UNIQUE NOT NULL,
                    contrasena TEXT NOT NULL,
                    rol TEXT NOT NULL
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS categorias (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nombre_categoria TEXT UNIQUE NOT NULL
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS productos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nombre TEXT NOT NULL,
                    precio REAL NOT NULL,
                    cantidad_stock REAL NOT NULL,
                    categoria_id INTEGER,
                    es_pack INTEGER DEFAULT 0,
                    unidades_por_pack REAL DEFAULT 1.0,
                    es_preparado INTEGER DEFAULT 0,
                    codigo_barras TEXT,
                    precio_unidad_venta REAL,
                    precio_medio_venta REAL,
                    FOREIGN KEY(categoria_id) REFERENCES categorias(id)
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS insumos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nombre_insumo TEXT NOT NULL,
                    cantidad_actual REAL DEFAULT 0,
                    unidad_medida TEXT DEFAULT 'Unid'
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS proveedores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nombre TEXT NOT NULL UNIQUE
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS recetas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    producto_id INTEGER NOT NULL,
                    insumo_id INTEGER NOT NULL,
                    cantidad_gastada REAL NOT NULL,
                    FOREIGN KEY(producto_id) REFERENCES productos(id),
                    FOREIGN KEY(insumo_id) REFERENCES insumos(id)
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ventas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fecha_hora DATETIME DEFAULT CURRENT_TIMESTAMP,
                    vendedor_id INTEGER,
                    total REAL NOT NULL,
                    efectivo_recibido REAL NOT NULL,
                    cambio REAL NOT NULL,
                    fecha TEXT,
                    metodo_pago TEXT DEFAULT 'efectivo',
                    referencia_pago TEXT,
                    FOREIGN KEY(vendedor_id) REFERENCES usuarios(id)
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS detalle_ventas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    venta_id INTEGER NOT NULL,
                    producto_id INTEGER NOT NULL,
                    cantidad INTEGER NOT NULL,
                    precio_unitario REAL NOT NULL,
                    FOREIGN KEY(venta_id) REFERENCES ventas(id),
                    FOREIGN KEY(producto_id) REFERENCES productos(id)
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS gastos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vendedor_id INTEGER,
                    descripcion TEXT NOT NULL,
                    monto REAL NOT NULL,
                    categoria_gasto TEXT DEFAULT 'Otros',
                    fecha TEXT,
                    FOREIGN KEY(vendedor_id) REFERENCES usuarios(id)
                );
            """)
            version_actual = 1
            cursor.execute("INSERT OR REPLACE INTO db_metadata (key, value) VALUES ('version', '1')")
            conn.commit()

        # ==========================================
        # MIGRACIÓN v2: Turnos, Arqueos, Fiados, Órdenes Abiertas, Clientes Exclusivos
        # ==========================================
        if version_actual < 2:
            print("INFO: Aplicando migración v2 (Módulo de turnos, arqueos y deudas)...")
            
            # Crear nuevas tablas de la versión 2
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS turnos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    usuario_id INTEGER NOT NULL,
                    nombre_usuario TEXT NOT NULL,
                    rol TEXT NOT NULL,
                    fecha_apertura TEXT NOT NULL,
                    fecha_cierre TEXT,
                    estado TEXT DEFAULT 'abierto',
                    efectivo_esperado REAL,
                    efectivo_real REAL,
                    diferencia REAL,
                    nequi_esperado REAL,
                    nequi_real REAL,
                    diferencia_nequi REAL,
                    daviplata_esperado REAL,
                    daviplata_real REAL,
                    diferencia_daviplata REAL,
                    tarjeta_esperado REAL,
                    tarjeta_real REAL,
                    diferencia_tarjeta REAL,
                    observaciones TEXT,
                    FOREIGN KEY(usuario_id) REFERENCES usuarios(id)
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ordenes_abiertas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mesa_cliente TEXT NOT NULL,
                    vendedor_id INTEGER,
                    turno_id INTEGER,
                    fecha_creacion TEXT NOT NULL,
                    estado TEXT DEFAULT 'abierta',
                    observaciones TEXT,
                    total REAL DEFAULT 0
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ordenes_abiertas_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    orden_id INTEGER NOT NULL,
                    producto_id INTEGER NOT NULL,
                    nombre_producto TEXT NOT NULL,
                    precio_unitario REAL NOT NULL,
                    cantidad INTEGER NOT NULL,
                    subtotal REAL NOT NULL,
                    FOREIGN KEY(orden_id) REFERENCES ordenes_abiertas(id) ON DELETE CASCADE
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS caja_movimientos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    turno_id INTEGER,
                    usuario_id INTEGER,
                    tipo_movimiento TEXT NOT NULL,
                    origen TEXT NOT NULL,
                    descripcion TEXT,
                    metodo_pago TEXT DEFAULT 'efectivo',
                    monto REAL NOT NULL,
                    fecha TEXT NOT NULL,
                    FOREIGN KEY(turno_id) REFERENCES turnos(id),
                    FOREIGN KEY(usuario_id) REFERENCES usuarios(id)
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS clientes_exclusivos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nombre TEXT NOT NULL UNIQUE,
                    activo INTEGER DEFAULT 1,
                    fecha_creacion TEXT
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS deudas_clientes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    venta_id INTEGER,
                    cliente_nombre TEXT NOT NULL,
                    monto_total REAL NOT NULL,
                    monto_pagado REAL DEFAULT 0,
                    saldo_pendiente REAL NOT NULL,
                    estado TEXT DEFAULT 'pendiente',
                    fecha_creacion TEXT,
                    fecha_actualizacion TEXT,
                    FOREIGN KEY(venta_id) REFERENCES ventas(id)
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS abonos_deuda (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cliente_nombre TEXT NOT NULL,
                    monto REAL NOT NULL,
                    metodo_pago TEXT DEFAULT 'efectivo',
                    referencia TEXT,
                    vendedor_id INTEGER,
                    turno_id INTEGER,
                    fecha TEXT,
                    FOREIGN KEY(vendedor_id) REFERENCES usuarios(id),
                    FOREIGN KEY(turno_id) REFERENCES turnos(id)
                );
            """)

            # Agregar columnas adicionales a 'ventas' (si no existen)
            columnas_ventas = [info['name'] for info in cursor.execute("PRAGMA table_info(ventas);").fetchall()]
            columnas_a_agregar_ventas = [
                ('fecha', 'TEXT'),
                ('metodo_pago', "TEXT DEFAULT 'efectivo'"),
                ('referencia_pago', 'TEXT'),
                ('turno_id', 'INTEGER'),
                ('cliente_fiado', 'TEXT'),
                ('estado_cobro', "TEXT DEFAULT 'pagado'"),
                ('tipo_venta', "TEXT DEFAULT 'producto'")
            ]
            for col_name, col_def in columnas_a_agregar_ventas:
                if col_name not in columnas_ventas:
                    cursor.execute(f"ALTER TABLE ventas ADD COLUMN {col_name} {col_def};")

            # Actualizar 'tipo_venta' por defecto
            cursor.execute("UPDATE ventas SET tipo_venta = 'producto' WHERE tipo_venta IS NULL OR TRIM(tipo_venta) = '';")
            cursor.execute("""
                UPDATE ventas
                SET tipo_venta = 'intangible'
                WHERE id NOT IN (SELECT DISTINCT venta_id FROM detalle_ventas)
                  AND COALESCE(cliente_fiado, '') = ''
                  AND COALESCE(estado_cobro, 'pagado') = 'pagado'
                  AND COALESCE(metodo_pago, 'efectivo') != 'fiado'
            """)

            # Agregar columnas adicionales a 'gastos'
            columnas_gastos = [info['name'] for info in cursor.execute("PRAGMA table_info(gastos);").fetchall()]
            if 'metodo_pago' not in columnas_gastos:
                cursor.execute("ALTER TABLE gastos ADD COLUMN metodo_pago TEXT DEFAULT 'efectivo';")
            if 'turno_id' not in columnas_gastos:
                cursor.execute("ALTER TABLE gastos ADD COLUMN turno_id INTEGER;")

            # Agregar columnas a 'productos'
            columnas_productos = [info['name'] for info in cursor.execute("PRAGMA table_info(productos);").fetchall()]
            if 'codigo_barras' not in columnas_productos:
                cursor.execute("ALTER TABLE productos ADD COLUMN codigo_barras TEXT;")
            if 'precio_unidad_venta' not in columnas_productos:
                cursor.execute("ALTER TABLE productos ADD COLUMN precio_unidad_venta REAL;")
            if 'precio_medio_venta' not in columnas_productos:
                cursor.execute("ALTER TABLE productos ADD COLUMN precio_medio_venta REAL;")

            version_actual = 2
            cursor.execute("INSERT OR REPLACE INTO db_metadata (key, value) VALUES ('version', '2')")
            conn.commit()

        # ==========================================
        # MIGRACIÓN v3: Columna de icono en categorías
        # ==========================================
        if version_actual < 3:
            print("INFO: Aplicando migración v3 (Agregar columna de icono en categorías)...")
            columnas_categorias = [info['name'] for info in cursor.execute("PRAGMA table_info(categorias);").fetchall()]
            if 'icono' not in columnas_categorias:
                cursor.execute("ALTER TABLE categorias ADD COLUMN icono TEXT;")
            version_actual = 3
            cursor.execute("INSERT OR REPLACE INTO db_metadata (key, value) VALUES ('version', '3')")
            conn.commit()

        # Asegurar que exista al menos un usuario administrador por defecto si la tabla está vacía
        cant_usuarios = cursor.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]
        if cant_usuarios == 0:
            cursor.execute("""
                INSERT INTO usuarios (nombre, usuario, contrasena, rol) 
                VALUES ('Administrador Principal', 'admin', 'admin123', 'administrador')
            """)
            conn.commit()
            print("INFO: Creado usuario administrador por defecto (admin / admin123).")

        print(f"INFO: Migracion exitosa. Base de datos actualizada a la version {version_actual}.")

    except Exception as migration_err:
        conn.rollback()
        print(f"ERROR: ERROR durante la migracion de base de datos: {migration_err}")
        
        # 5. Si hay un fallo crítico, cerrar la conexión y restaurar la copia de seguridad
        try:
            conn.close()
            if respaldo_path and os.path.exists(respaldo_path):
                shutil.copy2(respaldo_path, db_path)
                print("WARNING: Copia de respaldo restaurada exitosamente para preservar la estabilidad de los datos.")
        except Exception as restore_err:
            print(f"CRITICAL: No se pudo restaurar el respaldo de base de datos: {restore_err}")
        raise migration_err
    finally:
        try:
            conn.close()
        except Exception:
            pass

def encontrar_puerto_libre(puerto_preferido=8080):
    import socket
    # Intentamos primero con "0.0.0.0" (para permitir acceso desde otros equipos en red local)
    # Si falla por permisos (WinError 10013), reintentamos con "127.0.0.1" (solo local)
    for host in ["0.0.0.0", "127.0.0.1"]:
        for p in [puerto_preferido, 8081, 8082, 8888, 9080]:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((host, p))
                s.close()
                return p, host
            except OSError:
                continue
                
        # Si ninguno de los preferidos está libre, dejar que el SO asigne uno dinámico con el host actual
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, 0))
            puerto = s.getsockname()[1]
            s.close()
            return puerto, host
        except OSError:
            continue
            
    # Fallback de seguridad en caso extremo
    return puerto_preferido, "127.0.0.1"

def mostrar_error_gui(titulo, mensaje):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, mensaje, titulo, 0x10) # 0x10 es MB_ICONERROR
    except Exception:
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(titulo, mensaje)
            root.destroy()
        except Exception:
            pass

if __name__ == '__main__':
    try:
        # Inicialización y migración automática de la base de datos al arrancar el servidor
        inicializar_y_migrar_db()
        
        puerto, host = encontrar_puerto_libre(8080)
        
        if es_compilado:
            import webbrowser
            import threading
            import time
            
            def abrir_navegador():
                time.sleep(1.5) # Esperar a que waitress inicie
                try:
                    url_host = "127.0.0.1" if host == "0.0.0.0" else host
                    webbrowser.open(f"http://{url_host}:{puerto}")
                except Exception:
                    pass
                    
            threading.Thread(target=abrir_navegador, daemon=True).start()
            
            from waitress import serve
            serve(
                app,
                host=host,
                port=puerto,
                threads=16
            )
        else:
            # Servidor de desarrollo con debug=True y hot-reload
            app.run(
                host=host,
                port=puerto,
                debug=True
            )
    except Exception as e:
        import traceback
        error_msg = f"No se pudo iniciar la aplicación Cafeto24.\n\nDetalles del error:\n{e}\n\n{traceback.format_exc()}"
        try:
            print(f"CRITICAL ERROR ON STARTUP:\n{traceback.format_exc()}")
        except Exception:
            pass
        mostrar_error_gui("Error de Inicio - Cafeto24", error_msg)