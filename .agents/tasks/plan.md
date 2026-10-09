# Implementation Plan — Factura electrónica (sin DIAN) en POS Cafeto24

Objetivo: agregar un modo "Factura electrónica" (solo captura/guardado/impresión de datos del
cliente, SIN integración DIAN) en la pantalla de ventas, debajo del apartado de cobro. Modo
"Factura normal" por defecto. Cambios en 4 capas sobre dos archivos:
`CajaCafeto24.py` y `templates/ventas.html`.

## Hallazgos de la exploración (base para cada decisión)

- **Migraciones**: `CajaCafeto24.py` usa un sistema de migración VERSIONADO con `db_metadata(key='version')`.
  La versión actual máxima es **v6** (bloque `if version_actual < 6:` ~línea 5137, que cierra en
  ~línea 5156 con `INSERT ... 'version', '6'` + `conn.commit()`). Justo después viene el bloque
  "Asegurar que siempre exista el usuario administrador 'admin'". El patrón idempotente ya existente
  es: leer `columnas = [info['name'] for info in cursor.execute("PRAGMA table_info(ventas);").fetchall()]`
  y hacer `ALTER TABLE ... ADD COLUMN` solo si la columna falta (ver bloque v2, líneas ~5045-5057).
  **Decisión**: agregar una **MIGRACIÓN v7** siguiendo exactamente ese patrón, en lugar de tocar el
  `CREATE TABLE IF NOT EXISTS ventas` (que no altera tablas existentes en producción). La DB de
  desarrollo está en versión 6 y la tabla `ventas` NO tiene aún las columnas FE (verificado con
  `PRAGMA table_info`). El `CREATE TABLE IF NOT EXISTS ventas` (~línea 4897) se deja igual: en DB
  nuevas, la migración v7 corre después y añade las columnas de todas formas; mantener un único punto
  de verdad (la migración) evita divergencia.

- **`conectar_db()`** (línea ~188) fija `row_factory = sqlite3.Row`, por eso `venta['col']` e
  `info['name']` funcionan. En `imprimir_factura_directa` `venta` es un `sqlite3.Row`; para evitar
  `IndexError`/`KeyError` en DB muy viejas se consulta con `venta.keys()`.

- **`api_cobrar`** (ruta `/api/cobrar`, def ~línea 3962): recibe `data = request.json`. El INSERT del
  flujo de productos es el bloque `cursor_venta = db.execute("INSERT INTO ventas (...) VALUES (...)")`
  en ~líneas 4031-4049, con columnas explícitas:
  `(vendedor_id, turno_id, metodo_pago, total, efectivo_recibido, cambio, referencia_pago,
  cliente_fiado, estado_cobro, tipo_venta, fecha)` y `tipo_venta` fijado literal a `'producto'`,
  `fecha` con `datetime('now','localtime')`. Hay otros INSERT INTO ventas (fiado/intangibles ~1718,
  ~4564) que NO se tocan: las columnas FE son nullable con DEFAULT, así que esos inserts siguen válidos.

- **Frontend `ventas.html`**: la sección de cobro está en ~líneas 520-570. Contiene los botones de
  método de pago, paneles `#panel-digital`/`#panel-fiado`/`#panel-efectivo`, los botones Cobrar/Pago
  dividido (~líneas 557-561) y, al final (líneas 566-569), el `<label>` con el checkbox
  `#imprimir-factura`, seguido de `</div>` (cierre del panel de cobro) en línea 570. **Decisión**:
  insertar el control de "Factura normal / electrónica" + el bloque de campos JUSTO DESPUÉS del
  `<label>` de `#imprimir-factura` (antes del `</div>` de línea 570) — queda literalmente debajo del
  apartado de cobro, como pidió el usuario.
- **Dos envíos a `/api/cobrar`**: `cobrarPedido()` (~línea 3198, cobro normal) y `cobrarPagoDividido()`
  (~línea 3872, pago dividido). Ambos leen `#imprimir-factura` vía `document.getElementById`. Como el
  control FE vive en el panel de cobro (no en el modal dividido), ambas funciones podrán leerlo igual.
  Los **mensajes de error** en toda la página se muestran con `alert(...)` (p.ej. "⚠️ ..."); se usa el
  mismo mecanismo para la validación FE.
- El bloque del **logo raster** (`_logo_raster_escpos(ruta_logo, ancho_px=120)` ~línea 4370) NO se toca.

Decisión de estructura: es UNA sola feature cohesiva (las 4 capas comparten el mismo conjunto de
columnas FE y ninguna capa es útil por separado). No se descompone en FEATs; se implementa con este
plan y el loop existente lo ejecuta.

---

## Items (ordenados por dependencia)

- [ ] 1. **Migración v7 de base de datos**: agregar las 7 columnas FE a la tabla `ventas` de forma
      idempotente, siguiendo el patrón `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` del bloque v2.
      Insertar un bloque `if version_actual < 7:` INMEDIATAMENTE DESPUÉS del cierre del bloque v6
      (después de la línea `cursor.execute("INSERT OR REPLACE INTO db_metadata (key, value) VALUES ('version', '6')")`
      y su `conn.commit()`, ~línea 5156) y ANTES del comentario "Asegurar que siempre exista el usuario
      administrador 'admin'". Código a insertar:
      ```python
              # ==========================================
              # MIGRACIÓN v7: Datos de factura electrónica (sin integración DIAN)
              # ==========================================
              if version_actual < 7:
                  print("INFO: Aplicando migración v7 (Datos de factura electrónica en ventas)...")
                  columnas_ventas_v7 = [info['name'] for info in cursor.execute("PRAGMA table_info(ventas);").fetchall()]
                  columnas_fe = [
                      ('requiere_factura_electronica', 'INTEGER DEFAULT 0'),
                      ('fe_tipo_documento', 'TEXT'),
                      ('fe_numero_documento', 'TEXT'),
                      ('fe_nombre', 'TEXT'),
                      ('fe_direccion', 'TEXT'),
                      ('fe_telefono', 'TEXT'),
                      ('fe_correo', 'TEXT'),
                  ]
                  for col_name, col_def in columnas_fe:
                      if col_name not in columnas_ventas_v7:
                          cursor.execute(f"ALTER TABLE ventas ADD COLUMN {col_name} {col_def};")
                  version_actual = 7
                  cursor.execute("INSERT OR REPLACE INTO db_metadata (key, value) VALUES ('version', '7')")
                  conn.commit()
      ```
      Respetar la indentación real del archivo (los bloques `if version_actual < N:` están indentados
      dentro de la función; usar el mismo nivel que el bloque v6). No modificar el `CREATE TABLE IF NOT
      EXISTS ventas`.
      Files: `CajaCafeto24.py`
      Verify: `python -c "import py_compile; py_compile.compile('CajaCafeto24.py', doraise=True); print('OK')"`
      debe imprimir `OK`. Y correr el test de migración del item 6 (que crea una DB v6 temporal, corre
      la migración y confirma que aparecen las 7 columnas FE y `version='7'`).

- [ ] 2. **Backend: leer y validar campos FE en `api_cobrar`**. Dentro de `def api_cobrar()` (~línea 3962),
      después de la lectura de los campos existentes (`cliente_fiado = ...`, ~línea 3972) y antes del
      `if not carrito:`, agregar la lectura/normalización de los campos FE:
      ```python
          requiere_fe = 1 if data.get('requiere_factura_electronica') in (True, 1, '1', 'true', 'True') else 0
          fe_tipo_documento = (data.get('fe_tipo_documento') or '').strip()
          fe_numero_documento = (data.get('fe_numero_documento') or '').strip()
          fe_nombre = (data.get('fe_nombre') or '').strip()
          fe_direccion = (data.get('fe_direccion') or '').strip()
          fe_telefono = (data.get('fe_telefono') or '').strip()
          fe_correo = (data.get('fe_correo') or '').strip()
          if requiere_fe == 1 and (not fe_numero_documento or not fe_nombre):
              return jsonify({'error': 'Para factura electrónica se requiere documento y nombre', 'success': False}), 400
      ```
      Cuando `requiere_fe == 0`, los `fe_*` quedan como `''` y no se validan (se guardarán como 0/''/None
      en el item 3).
      Files: `CajaCafeto24.py`
      Verify: `python -c "import py_compile; py_compile.compile('CajaCafeto24.py', doraise=True); print('OK')"`
      imprime `OK`.

- [ ] 3. **Backend: agregar columnas FE al INSERT del flujo de productos**. Modificar el bloque
      `cursor_venta = db.execute("INSERT INTO ventas (...) VALUES (...)", (...))` (~líneas 4031-4049).
      Añadir las 7 columnas al listado y los placeholders, y sus valores a la tupla de parámetros.
      Resultado esperado del INSERT (manteniendo `tipo_venta`='producto' y `fecha` literales):
      ```python
          cursor_venta = db.execute("""
              INSERT INTO ventas (vendedor_id, turno_id, metodo_pago, total, efectivo_recibido, cambio, referencia_pago, cliente_fiado, estado_cobro, tipo_venta, fecha,
                  requiere_factura_electronica, fe_tipo_documento, fe_numero_documento, fe_nombre, fe_direccion, fe_telefono, fe_correo)
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'producto', datetime('now', 'localtime'),
                  ?, ?, ?, ?, ?, ?, ?)
          """, (
              vendedor_id,
              turno_id,
              metodo_pago,
              total_venta,
              efectivo_recibido_val,
              cambio_val,
              referencia_pago,
              (cliente_fiado if metodo_pago == 'fiado' else None),
              ('pendiente' if metodo_pago == 'fiado' else 'pagado'),
              requiere_fe,
              (fe_tipo_documento if requiere_fe == 1 else None),
              (fe_numero_documento if requiere_fe == 1 else None),
              (fe_nombre if requiere_fe == 1 else None),
              (fe_direccion if requiere_fe == 1 else None),
              (fe_telefono if requiere_fe == 1 else None),
              (fe_correo if requiere_fe == 1 else None),
          ))
      ```
      No tocar los demás INSERT INTO ventas (fiado/intangibles ~1718, ~4564): sus columnas FE toman los
      DEFAULT (0/NULL) de la migración.
      Files: `CajaCafeto24.py`
      Verify: `python -c "import py_compile; py_compile.compile('CajaCafeto24.py', doraise=True); print('OK')"`
      imprime `OK`.

- [ ] 4. **Impresión: añadir sección de datos FE en `imprimir_factura_directa`**. En la función
      (def ~línea 4303), después del bloque de método de pago/cambio y su separador
      `raw_data.extend(b"--------------------------------\n")` (~línea 4431) y ANTES de
      `raw_data.extend(centrado + b"Gracias por su compra\n")` (~línea 4432), insertar la sección FE,
      alineada a la izquierda, omitiendo campos vacíos y usando `.encode('latin1', errors='replace')`:
      ```python
                  # Datos de factura electronica (si aplica)
                  venta_keys = venta.keys()
                  if ('requiere_factura_electronica' in venta_keys) and (venta['requiere_factura_electronica'] == 1):
                      raw_data.extend(izquierda)
                      raw_data.extend(b"--------------------------------\n")
                      raw_data.extend(b"DATOS FACTURA ELECTRONICA\n")
                      fe_tipo = (venta['fe_tipo_documento'] or '').strip()
                      fe_num = (venta['fe_numero_documento'] or '').strip()
                      if fe_num:
                          raw_data.extend(f"Documento: {fe_tipo} {fe_num}\n".strip().encode('latin1', errors='replace') + b"\n")
                      if (venta['fe_nombre'] or '').strip():
                          raw_data.extend(f"Nombre: {venta['fe_nombre']}\n".encode('latin1', errors='replace'))
                      if (venta['fe_direccion'] or '').strip():
                          raw_data.extend(f"Direccion: {venta['fe_direccion']}\n".encode('latin1', errors='replace'))
                      if (venta['fe_telefono'] or '').strip():
                          raw_data.extend(f"Tel: {venta['fe_telefono']}\n".encode('latin1', errors='replace'))
                      if (venta['fe_correo'] or '').strip():
                          raw_data.extend(f"Correo: {venta['fe_correo']}\n".encode('latin1', errors='replace'))
      ```
      (La línea Documento une tipo+número; el `.strip()` evita el espacio inicial si `fe_tipo` viene
      vacío. El separador es de 32 guiones = papel 58mm/32 columnas.) Respetar la indentación real del
      bloque (está dentro de `with`/`try` anidados; usar el mismo nivel que las líneas `raw_data.extend`
      vecinas). NO tocar el bloque del logo raster.
      Files: `CajaCafeto24.py`
      Verify: `python -c "import py_compile; py_compile.compile('CajaCafeto24.py', doraise=True); print('OK')"`
      imprime `OK`. (No se puede imprimir realmente: el python de desarrollo no tiene `win32print`; no
      ejecutar la app Flask.)

- [ ] 5. **Frontend: control FE + campos + envío + validación en `ventas.html`**.
      (a) **Markup**: justo después del `<label>` del checkbox `#imprimir-factura` (líneas 566-569) y
      antes del `</div>` que cierra el panel de cobro (línea 570), insertar el control de marcado y el
      bloque de campos (oculto por defecto con la clase `hidden` que ya usa el resto del archivo):
      ```html
                          <!-- MODO DE FACTURA: normal vs electronica -->
                          <div class="bg-slate-50 border rounded-xl px-2.5 py-2 shadow-sm space-y-2">
                              <div class="flex gap-2 text-[11px] font-bold text-gray-600">
                                  <label class="flex items-center gap-1.5 cursor-pointer">
                                      <input type="radio" name="modo-factura" value="normal" checked
                                             onchange="toggleFacturaElectronica()" class="h-3.5 w-3.5 text-green-600 focus:ring-green-500">
                                      <span>🧾 Factura normal</span>
                                  </label>
                                  <label class="flex items-center gap-1.5 cursor-pointer">
                                      <input type="radio" name="modo-factura" value="electronica"
                                             onchange="toggleFacturaElectronica()" class="h-3.5 w-3.5 text-green-600 focus:ring-green-500">
                                      <span>📧 Factura electrónica</span>
                                  </label>
                              </div>
                              <div id="panel-factura-electronica" class="hidden space-y-1.5 border-t border-slate-200 pt-2">
                                  <div class="grid grid-cols-2 gap-2">
                                      <div>
                                          <label class="block text-[9px] font-bold text-gray-400 uppercase">Tipo Documento</label>
                                          <select id="fe-tipo-documento" class="w-full bg-white border border-slate-300 p-1.5 rounded-lg text-xs text-gray-700 outline-none">
                                              <option value="CC">CC</option>
                                              <option value="NIT">NIT</option>
                                              <option value="CE">CE</option>
                                              <option value="Pasaporte">Pasaporte</option>
                                          </select>
                                      </div>
                                      <div>
                                          <label class="block text-[9px] font-bold text-gray-400 uppercase">N° Documento</label>
                                          <input type="text" id="fe-numero-documento" class="w-full bg-white border border-slate-300 p-1.5 rounded-lg text-xs text-gray-700 outline-none">
                                      </div>
                                  </div>
                                  <label class="block text-[9px] font-bold text-gray-400 uppercase">Nombre / Razón social</label>
                                  <input type="text" id="fe-nombre" class="w-full bg-white border border-slate-300 p-1.5 rounded-lg text-xs text-gray-700 outline-none">
                                  <label class="block text-[9px] font-bold text-gray-400 uppercase">Dirección</label>
                                  <input type="text" id="fe-direccion" class="w-full bg-white border border-slate-300 p-1.5 rounded-lg text-xs text-gray-700 outline-none">
                                  <div class="grid grid-cols-2 gap-2">
                                      <div>
                                          <label class="block text-[9px] font-bold text-gray-400 uppercase">Teléfono</label>
                                          <input type="text" id="fe-telefono" class="w-full bg-white border border-slate-300 p-1.5 rounded-lg text-xs text-gray-700 outline-none">
                                      </div>
                                      <div>
                                          <label class="block text-[9px] font-bold text-gray-400 uppercase">Correo</label>
                                          <input type="text" id="fe-correo" class="w-full bg-white border border-slate-300 p-1.5 rounded-lg text-xs text-gray-700 outline-none">
                                      </div>
                                  </div>
                              </div>
                          </div>
      ```
      (b) **JS helpers**: agregar dos funciones dentro del `<script>` existente (p.ej. cerca de
      `cobrarPedido`, respetando el estilo JS vanilla inline). Una para mostrar/ocultar el panel y otra
      para armar el objeto FE y validar:
      ```javascript
              function toggleFacturaElectronica() {
                  const esElectronica = document.querySelector('input[name="modo-factura"]:checked')?.value === 'electronica';
                  const panel = document.getElementById('panel-factura-electronica');
                  if (panel) panel.classList.toggle('hidden', !esElectronica);
              }

              // Devuelve {ok, data} con los campos FE para el body del fetch; valida si es electronica.
              function obtenerDatosFacturaElectronica() {
                  const esElectronica = document.querySelector('input[name="modo-factura"]:checked')?.value === 'electronica';
                  if (!esElectronica) {
                      return { ok: true, data: { requiere_factura_electronica: 0 } };
                  }
                  const numero = (document.getElementById('fe-numero-documento').value || '').trim();
                  const nombre = (document.getElementById('fe-nombre').value || '').trim();
                  if (!numero || !nombre) {
                      alert('⚠️ Para factura electrónica debes ingresar número de documento y nombre.');
                      return { ok: false, data: null };
                  }
                  return {
                      ok: true,
                      data: {
                          requiere_factura_electronica: 1,
                          fe_tipo_documento: document.getElementById('fe-tipo-documento').value,
                          fe_numero_documento: numero,
                          fe_nombre: nombre,
                          fe_direccion: (document.getElementById('fe-direccion').value || '').trim(),
                          fe_telefono: (document.getElementById('fe-telefono').value || '').trim(),
                          fe_correo: (document.getElementById('fe-correo').value || '').trim(),
                      }
                  };
              }

              function limpiarFacturaElectronica() {
                  const radioNormal = document.querySelector('input[name="modo-factura"][value="normal"]');
                  if (radioNormal) radioNormal.checked = true;
                  ['fe-numero-documento','fe-nombre','fe-direccion','fe-telefono','fe-correo'].forEach(id => {
                      const el = document.getElementById(id); if (el) el.value = '';
                  });
                  const tipo = document.getElementById('fe-tipo-documento'); if (tipo) tipo.value = 'CC';
                  toggleFacturaElectronica();
              }
      ```
      (c) **`cobrarPedido()`** (~línea 3198): antes del `fetch('/api/cobrar'...)` agregar
      `const fe = obtenerDatosFacturaElectronica(); if (!fe.ok) return;` y en el objeto del
      `body: JSON.stringify({ ... })` hacer spread de los campos FE, p.ej. añadir `...fe.data,` dentro
      del objeto. Tras una venta exitosa (donde ya resetea `#imprimir-factura` ~línea 3264) llamar
      `limpiarFacturaElectronica();`.
      (d) **`cobrarPagoDividido()`** (~línea 3872): mismo patrón — `const fe = obtenerDatosFacturaElectronica();
      if (!fe.ok) return;` antes del fetch, `...fe.data,` en el body JSON, y `limpiarFacturaElectronica();`
      en el reseteo tras éxito (~línea 3924).
      (e) **`cancelarPedido()`** (~línea 2816): añadir `limpiarFacturaElectronica();` junto a los demás
      resets para que el modo vuelva a "normal" al cancelar.
      Mantener "normal" como modo por defecto (el radio ya tiene `checked`), de modo que el flujo actual
      no cambie si el cajero no toca nada.
      Files: `templates/ventas.html`
      Verify (ambos comandos, confirmados en este entorno):
      1. Balance de llaves del `<script>` con el probe del repo: `python check_js.py` — NO debe imprimir
         ninguna línea "LLAVE EXTRA" (ese script reporta llaves `{}` desbalanceadas dentro del `<script>`).
      2. Carga de la plantilla sin romper Jinja:
         `python -c "from jinja2 import Environment, FileSystemLoader; Environment(loader=FileSystemLoader('templates')).get_template('ventas.html'); print('TEMPLATE OK')"`
         debe imprimir `TEMPLATE OK` (verificado: funciona con el python de desarrollo).

- [ ] 6. **Test de migración (sqlite3 stdlib, sin win32print, sin Flask)**. Crear un script de prueba en
      `scratch/test_migracion_fe.py` que: (1) cree una DB temporal con una tabla `ventas` al estilo v6
      (columnas actuales: id, fecha_hora, vendedor_id, total, efectivo_recibido, cambio, fecha,
      metodo_pago, referencia_pago, turno_id, cliente_fiado, estado_cobro, tipo_venta) y
      `db_metadata(key='version', value='6')`; (2) ejecute SOLO la lógica de la migración v7 (puede
      replicar el bloque `ALTER TABLE` del item 1 contra esa DB, o importar la función de inicialización
      si es aislable sin arrancar Flask); (3) verifique con `PRAGMA table_info(ventas)` que existen las 7
      columnas FE y que `version` quedó en `'7'`; (4) inserte una venta con `requiere_factura_electronica=1`
      y lea de vuelta los campos; (5) corra la migración DOS veces y confirme idempotencia (no lanza, no
      duplica columnas).
      Files: `scratch/test_migracion_fe.py`
      Verify: `python scratch/test_migracion_fe.py` termina con código 0 e imprime que las 7 columnas FE
      existen, `version='7'`, el insert FE se lee correctamente y la segunda corrida es idempotente.

- [ ] 7. **Verificación final de compilación**. Tras aplicar los items 1-5, confirmar que el archivo
      principal compila y que la plantilla carga.
      Files: (ninguno nuevo)
      Verify:
      `python -c "import py_compile; py_compile.compile('CajaCafeto24.py', doraise=True); print('COMPILE OK')"`
      imprime `COMPILE OK`, y el chequeo de plantilla del item 5(e) imprime `TEMPLATE OK`. NO ejecutar la
      app Flask ni la impresión (sin `win32print` en el entorno de desarrollo).

## Notas / supuestos

- No se integra DIAN; no se genera XML/CUFE/QR/PDF. Solo captura, guardado e impresión de texto.
- El logo raster (`static/logo_factura.png`, 120px) queda intacto.
- Papel 58mm = 32 columnas; el separador de la sección FE usa 32 guiones, igual que el resto del ticket.
- Los otros flujos de inserción de ventas (fiado/intangibles) no se modifican; sus columnas FE toman
  los DEFAULT de la migración (0/NULL).
- Supuesto razonable: la sección FE del ticket se imprime tras el bloque de pago/cambio y antes del
  pie "Gracias por su compra", que es la ubicación natural para datos del cliente en el ticket.
- Build/compilación completa del instalador (lo que el usuario pidió para recibir la actualización) es
  un paso posterior fuera de este plan de código; aquí solo se dejan los cambios de código verificados
  con py_compile y el test de migración.

---

## Nota de verificación (iteración 1 — implementación completa)

Cambios aplicados (todas las 4 capas):

- **CajaCafeto24.py**
  - Migración v7 (~líneas 5195-5215): nuevo bloque `if version_actual < 7:` tras el cierre de v6
    y antes de "Asegurar que siempre exista el usuario administrador 'admin'". Usa
    `PRAGMA table_info(ventas)` + `ALTER TABLE ... ADD COLUMN` idempotente para las 7 columnas FE;
    fija `version='7'`. No se tocó `CREATE TABLE IF NOT EXISTS ventas`.
  - `api_cobrar` (~líneas 3974-3983): lectura/normalización de `requiere_factura_electronica` y
    `fe_*`; validación 400 `{'error': 'Para factura electrónica se requiere documento y nombre',
    'success': False}` si `requiere_fe==1` y falta documento o nombre.
  - INSERT del flujo de productos (~líneas 4042-4064): añadidas las 7 columnas FE y sus valores;
    con `requiere_fe==0` guarda 0 y None. Los INSERT de fiado/intangibles NO se tocaron.
  - `imprimir_factura_directa` (~líneas 4458-4479): sección "DATOS FACTURA ELECTRONICA" tras el
    bloque de pago/cambio y antes de "Gracias por su compra", solo si
    `venta['requiere_factura_electronica']==1`; separador de 32 guiones (58mm), alineación
    `izquierda`, `.encode('latin1', errors='replace')`, campos vacíos omitidos. Logo raster intacto.

- **templates/ventas.html**
  - Markup del control radio normal/electrónica + `#panel-factura-electronica` (oculto con `hidden`)
    insertado debajo del `<label>` de `#imprimir-factura`, dentro del panel de cobro.
  - Funciones JS vanilla: `toggleFacturaElectronica()`, `obtenerDatosFacturaElectronica()`
    (valida documento+nombre con `alert`), `limpiarFacturaElectronica()`.
  - `cobrarPedido()` y `cobrarPagoDividido()`: `const fe = obtenerDatosFacturaElectronica();
    if (!fe.ok) return;` antes del fetch, `...fe.data` en el body JSON, y
    `limpiarFacturaElectronica()` en el reseteo tras éxito. `cancelarPedido()` también resetea FE.
  - Modo "normal" por defecto (radio `checked`).

Comandos ejecutados (Windows/PowerShell, `python` = 3.12 sin win32print; NO se ejecutó Flask ni impresión):

1. `python -c "import py_compile; py_compile.compile('CajaCafeto24.py', doraise=True); print('COMPILE OK')"`
   → `COMPILE OK`, exit 0.
2. Script temporal `scratch/test_migracion_fe.py` (sqlite3 stdlib): creó DB v6 (sin columnas FE),
   aplicó la migración v7, confirmó por `PRAGMA table_info(ventas)` que existen las 7 columnas FE,
   `version='7'`, insert con `requiere_factura_electronica=1` legible, y 2ª corrida idempotente.
   → `TODAS LAS PRUEBAS PASARON`, exit 0. Script BORRADO al terminar.
3. `python -c "from jinja2 import Environment, FileSystemLoader; ...get_template('ventas.html')..."`
   → `TEMPLATE OK`, exit 0.
4. `python check_js.py` → sin líneas "LLAVE EXTRA" (JS balanceado), exit 0.

Verificación estática: el body JSON de `cobrarPedido`/`cobrarPagoDividido` incluye los campos FE vía
`...fe.data`; el código de impresión arma las líneas solo con `requiere_factura_electronica==1` y las
omite con `==0` (y omite líneas de campos vacíos).
