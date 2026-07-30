import sqlite3
import pandas as pd
import os

def sincronizar_desde_excel():
    # 1. Buscar cualquier archivo .xlsx o .csv en la carpeta
    archivos = [f for f in os.listdir('.') if f.endswith(('.xlsx', '.csv'))]
    if not archivos:
        print("❌ ERROR: No se encontró ningún archivo de datos en la carpeta.")
        return
    
    archivo = archivos[0]
    print(f"✅ Procesando archivo: {archivo}")

    # 2. Leer archivo (Pandas detecta automáticamente si es Excel o CSV)
    if archivo.endswith('.xlsx'):
        df = pd.read_excel(archivo)
    else:
        df = pd.read_csv(archivo)

    # 3. Conectar a la base de datos
    conexion = sqlite3.connect("cafeteria.db")
    cursor = conexion.cursor()

    # 4. Limpiar datos antiguos
    cursor.execute("DELETE FROM productos")
    cursor.execute("DELETE FROM categorias")

    print("-> Limpiando base de datos antigua...")

    # 5. Insertar datos
    for _, fila in df.iterrows():
        nombre = str(fila['Nombre'])
        # Algunos nombres vienen como NaN en el archivo, los filtramos
        if nombre == 'nan': continue
            
        cat = str(fila['Categoría del producto'] if pd.notna(fila['Categoría del producto']) else "Sin Categoría")
        precio = float(fila['Precio de venta'] if pd.notna(fila['Precio de venta']) else 0)
        stock = float(fila['Cantidad a la mano'] if pd.notna(fila['Cantidad a la mano']) else 0)
        
        # Insertar o ignorar categoría
        cursor.execute("INSERT OR IGNORE INTO categorias (nombre_categoria) VALUES (?)", (cat,))
        cursor.execute("SELECT id FROM categorias WHERE nombre_categoria = ?", (cat,))
        cat_id = cursor.fetchone()[0]
        
        # Insertar producto
        cursor.execute("""
            INSERT INTO productos (nombre, precio, cantidad_stock, categoria_id) 
            VALUES (?, ?, ?, ?)
        """, (nombre, precio, stock, cat_id))
    
    conexion.commit()
    conexion.close()
    print("🚀 ¡Base de datos actualizada correctamente con tus datos de Odoo!")

if __name__ == "__main__":
    sincronizar_desde_excel()