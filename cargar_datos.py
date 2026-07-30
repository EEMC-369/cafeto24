import sqlite3

def inicializar_estructura():
    conexion = sqlite3.connect("cafeteria.db")
    cursor = conexion.cursor()
    print("=== CONFIGURANDO ESTRUCTURA BASE DE DATOS ===")

    # 1. Usuarios
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        usuario TEXT UNIQUE NOT NULL,
        contrasena TEXT NOT NULL,
        rol TEXT NOT NULL
    );
    """)

    # 2. Categorías
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS categorias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre_categoria TEXT UNIQUE NOT NULL
    );
    """)

    # 3. Productos
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
        FOREIGN KEY(categoria_id) REFERENCES categorias(id)
    );
    """)

    # 4. Insumos
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS insumos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre_insumo TEXT NOT NULL,
        cantidad_actual REAL DEFAULT 0,
        unidad_medida TEXT DEFAULT 'Unid'
    );
    """)

    # 5. Proveedores
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS proveedores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL UNIQUE
    );
    """)

    # 6. Ventas
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

    # 7. Detalle de Ventas
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

    # 8. Gastos
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

    # 9. Recetas
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
    
    conexion.commit()
    conexion.close()
    print("✅ Estructura creada correctamente. La base está lista.")

if __name__ == "__main__":
    inicializar_estructura()