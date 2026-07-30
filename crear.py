import sqlite3

def vincular_categorias():
    conn = sqlite3.connect('cafeteria.db')
    cursor = conn.cursor()
    
    # 1. Crear tabla de categorías si no existe
    cursor.execute("CREATE TABLE IF NOT EXISTS categorias (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre_categoria TEXT UNIQUE)")
    
    # Verificamos si la columna 'categoria_nombre' existe en la tabla 'productos'
    cursor.execute("PRAGMA table_info(productos)")
    columnas_productos = [col[1] for col in cursor.fetchall()]
    
    if 'categoria_nombre' in columnas_productos:
        # 2. Obtener todas las categorías únicas de la columna temporal 'categoria_nombre'
        cursor.execute("SELECT DISTINCT categoria_nombre FROM productos WHERE categoria_nombre IS NOT NULL")
        categorias = cursor.fetchall()
        
        for cat in categorias:
            nombre = cat[0]
            if nombre:
                # Insertar categoría
                cursor.execute("INSERT OR IGNORE INTO categorias (nombre_categoria) VALUES (?)", (nombre,))
                # Obtener el ID asignado
                cursor.execute("SELECT id FROM categorias WHERE nombre_categoria = ?", (nombre,))
                cat_id = cursor.fetchone()[0]
                # Actualizar productos
                cursor.execute("UPDATE productos SET categoria_id = ? WHERE categoria_nombre = ?", (cat_id, nombre))
        print("¡Categorías vinculadas exitosamente!")
    else:
        print("La columna temporal 'categoria_nombre' no existe en 'productos'. No se requiere vinculación.")
        
    conn.commit()
    conn.close()

if __name__ == "__main__":
    vincular_categorias()