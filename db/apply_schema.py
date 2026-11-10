"""Aplica db/schema.sql a la base de datos definida en .env"""
import os
from pathlib import Path
import psycopg2
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

db_url = os.environ["DATABASE_URL"]
schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

conn = psycopg2.connect(db_url)
conn.autocommit = True
with conn.cursor() as cur:
    cur.execute(schema)
conn.close()
print("Schema aplicado correctamente.")
