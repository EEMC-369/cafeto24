import sqlite3
import os
import sys

# Forzar codificación UTF-8 en consola para evitar UnicodeEncodeError en Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def verificar():
    db_path = "cafeteria.db"
    if not os.path.exists(db_path):
        print(f"❌ El archivo {db_path} no existe en esta carpeta.")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    tablas = [
        'insumos', 
        'proveedores', 
        'usuarios', 
        'productos', 
        'categorias',
        'gastos',
        'ventas',
        'detalle_ventas',
        'recetas'
    ]
    
    print(f"--- ESTADO DE LA BASE DE DATOS: {db_path} ---")
    for tabla in tablas:
        try:
            cursor.execute(f"SELECT count(*) FROM {tabla}")
            cantidad = cursor.fetchone()[0]
            print(f"✅ Tabla '{tabla}': {cantidad} registros.")
        except sqlite3.OperationalError:
            print(f"⚠️ Tabla '{tabla}': No existe.")
    
    conn.close()

if __name__ == "__main__":
    verificar()