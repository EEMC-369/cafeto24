import pandas as pd
import sqlite3
import os

def migrar():
    # Buscamos archivos que contengan "Producto" en el nombre
    archivos = [f for f in os.listdir('.') if "Producto" in f]
    if not archivos:
        print("¡ERROR! No encuentro archivos que contengan 'Producto'.")
        return

    archivo = archivos[0]
    print(f"--- Procesando: {archivo} ---")
    
    # IMPORTANTE: Usamos read_excel, no read_csv
    # 'openpyxl' es el motor que lee archivos .xlsx
    df = pd.read_excel(archivo, engine='openpyxl')
    
    conn = sqlite3.connect('cafeteria.db') 
    cursor = conn.cursor()
    
    # Asegurar que existe la categoría "Sin Categoría" y obtener su ID
    cursor.execute("INSERT OR IGNORE INTO categorias (nombre_categoria) VALUES ('Sin Categoría')")
    cursor.execute("SELECT id FROM categorias WHERE nombre_categoria = 'Sin Categoría'")
    cat_id = cursor.fetchone()[0]
    
    count = 0
    for _, fila in df.iterrows():
        try:
            nombre = str(fila['Nombre'])
            precio = float(fila['Precio de venta']) if pd.notna(fila['Precio de venta']) else 0
            stock = float(fila['Cantidad a la mano']) if pd.notna(fila['Cantidad a la mano']) else 0
            
            # Usamos tus columnas validadas: nombre, precio, cantidad_stock, categoria_id
            cursor.execute("""
                INSERT OR REPLACE INTO productos (nombre, precio, cantidad_stock, categoria_id) 
                VALUES (?, ?, ?, ?)
            """, (nombre, precio, stock, cat_id))
            count += 1
        except Exception as e:
            print(f"Error en fila {count}: {e}")
            
    conn.commit()
    conn.close()
    print(f"¡ÉXITO! Se migraron {count} productos a la tabla 'productos'.")

migrar()