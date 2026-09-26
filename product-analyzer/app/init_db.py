import os

import psycopg2


SQL_FILES = [
    "sql/001_schema.sql",
    "sql/002_monetization.sql",
    "sql/003_3d_models.sql",
    "sql/004_wompi.sql",
    "sql/005_source_metadata.sql",
    "sql/007_tiktok_creator.sql",
    "sql/008_catalog_lifecycle.sql",
]


def init_database():
    db_url = os.getenv("DATABASE_URL")
    conn = psycopg2.connect(db_url) if db_url else psycopg2.connect(
        host=os.getenv("DB_HOST", "db"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "productos_db"),
        user=os.getenv("DB_USER", "productos_user"),
        password=os.getenv("DB_PASSWORD", "productos_pass"),
    )
    try:
        with conn.cursor() as cursor:
            for sql_file in SQL_FILES:
                path_to_sql = os.path.join(os.path.dirname(__file__), sql_file)
                print(f"Ejecutando {sql_file}...")
                with open(path_to_sql, "r", encoding="utf-8") as file:
                    cursor.execute(file.read())
        conn.commit()
        print("Base de datos inicializada correctamente")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    init_database()
