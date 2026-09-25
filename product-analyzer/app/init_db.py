import os
import psycopg2


def init_database():
    """Ejecuta los scripts SQL de inicialización."""
    conn = None
    try:
        # Soporte para DATABASE_URL (Producción) o parámetros individuales (Local)
        db_url = os.getenv("DATABASE_URL")
        if db_url:
            conn = psycopg2.connect(db_url)
        else:
            conn = psycopg2.connect(
                host=os.getenv("DB_HOST", "db"),
                port=os.getenv("DB_PORT", "5432"),
                dbname=os.getenv("DB_NAME", "productos_db"),
                user=os.getenv("DB_USER", "productos_user"),
                password=os.getenv("DB_PASSWORD", "productos_pass"),
            )

        # Desactivamos el autocommit para manejar la transacción de forma manual
        conn.autocommit = False
        cursor = conn.cursor()

        sql_files = [
            "sql/001_schema.sql",
            "sql/002_monetization.sql",
            "sql/003_3d_models.sql",
            "sql/004_wompi.sql",
            "sql/005_source_metadata.sql",
            "sql/006_curated_links.sql"
        ]

        for sql_file in sql_files:
            path_to_sql = os.path.join(os.path.dirname(__file__), sql_file)
            print(f"Ejecutando {sql_file}...")
            with open(path_to_sql, 'r') as f:
                sql_script = f.read()
                cursor.execute(sql_script)
                print(f"✓ Ejecutado: {sql_file}")

        # Si todo sale bien, guardamos los cambios de manera global
        conn.commit()
        print("✓ Base de datos inicializada correctamente")

    except Exception as e:
        # Si algo falla, revertimos cualquier cambio parcial para mantener la consistencia
        if conn:
            conn.rollback()
        print(f"✗ Error al inicializar BD: {e}")

    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if conn:
            conn.close()


if __name__ == "__main__":
    init_database()

