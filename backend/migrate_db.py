import sys
import os
import argparse
from sqlalchemy import create_engine, MetaData, Table, text

def parse_args():
    parser = argparse.ArgumentParser(description="Migrate data from SQLite to MySQL/MariaDB.")
    parser.add_argument("--sqlite", default="sqlite:///./spanel.db", help="SQLite database URL")
    parser.add_argument("--mysql", required=True, help="MySQL database URL (e.g., mysql+pymysql://user:pass@localhost/spanel)")
    return parser.parse_args()

def main():
    args = parse_args()
    sqlite_url = args.sqlite
    mysql_url = args.mysql

    print(f"[*] Connecting to SQLite: {sqlite_url}")
    sqlite_engine = create_engine(sqlite_url)
    sqlite_meta = MetaData()
    sqlite_meta.reflect(bind=sqlite_engine)

    print(f"[*] Connecting to MySQL: {mysql_url}")
    mysql_engine = create_engine(mysql_url)
    
    # Optional: Run Alembic migrations on MySQL to ensure schema is exactly up to date
    # os.environ["DATABASE_URL"] = mysql_url
    # from alembic.config import Config
    # from alembic import command
    # alembic_cfg = Config("alembic.ini")
    # print("[*] Running Alembic upgrade on MySQL...")
    # command.upgrade(alembic_cfg, "head")

    mysql_meta = MetaData()
    mysql_meta.reflect(bind=mysql_engine)

    # Disable foreign key checks for MySQL during import
    with mysql_engine.begin() as mysql_conn:
        mysql_conn.execute(text("SET FOREIGN_KEY_CHECKS=0;"))
        
        for table_name in sqlite_meta.tables:
            sqlite_table = sqlite_meta.tables[table_name]
            # Skip alembic version if we want, or just copy it too so the versions match
            
            print(f"[*] Migrating table: {table_name}")
            
            with sqlite_engine.connect() as sqlite_conn:
                rows = sqlite_conn.execute(sqlite_table.select()).fetchall()
                if not rows:
                    continue
                
                # We need the table definition in MySQL
                if table_name not in mysql_meta.tables:
                    print(f"  [!] Table {table_name} not found in MySQL schema. Creating it...")
                    sqlite_table.create(bind=mysql_engine)
                    mysql_meta.reflect(bind=mysql_engine)

                mysql_table = mysql_meta.tables[table_name]
                
                # Clear existing data in MySQL table
                mysql_conn.execute(mysql_table.delete())
                
                # Insert data in chunks
                chunk_size = 500
                dicts = [row._mapping for row in rows]
                
                for i in range(0, len(dicts), chunk_size):
                    chunk = dicts[i:i+chunk_size]
                    mysql_conn.execute(mysql_table.insert(), chunk)
                    
            print(f"  [+] Copied {len(dicts)} rows.")
            
        mysql_conn.execute(text("SET FOREIGN_KEY_CHECKS=1;"))
        
    print("[*] Migration completed successfully!")
    print("[*] You can now update your .env file with:")
    print(f"    DATABASE_URL={mysql_url}")

if __name__ == "__main__":
    main()
