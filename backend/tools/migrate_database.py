import argparse
import sys
import logging
from sqlalchemy import create_engine, MetaData
from sqlalchemy.orm import sessionmaker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("db_migrate")

def copy_data(source_engine, target_engine):
    source_meta = MetaData()
    source_meta.reflect(bind=source_engine)
    
    target_meta = MetaData()
    target_meta.reflect(bind=target_engine)

    with target_engine.begin() as target_conn:
        with source_engine.connect() as source_conn:
            for table_name, table in source_meta.tables.items():
                if table_name not in target_meta.tables:
                    logger.warning(f"Table {table_name} not found in target database. Skipping.")
                    continue
                
                logger.info(f"Copying table: {table_name}")
                # Clear target table
                target_table = target_meta.tables[table_name]
                target_conn.execute(target_table.delete())
                
                # Fetch all rows from source
                result = source_conn.execute(table.select())
                rows = result.fetchall()
                if not rows:
                    continue
                
                # Insert rows in chunks
                chunk_size = 1000
                for i in range(0, len(rows), chunk_size):
                    chunk = rows[i:i + chunk_size]
                    # Map Row to dict
                    dicts = [dict(zip(result.keys(), row)) for row in chunk]
                    target_conn.execute(target_table.insert(), dicts)
                
                logger.info(f"Copied {len(rows)} rows into {table_name}")

def main():
    parser = argparse.ArgumentParser(description="Migrate SPanel database data from SQLite to MySQL/MariaDB (or vice versa).")
    parser.add_argument("--source", required=True, help="Source database URL (e.g. sqlite:///./spanel.db)")
    parser.add_argument("--target", required=True, help="Target database URL (e.g. mysql+pymysql://user:pass@localhost/spanel)")
    
    args = parser.parse_args()
    
    if args.source == args.target:
        logger.error("Source and target database URLs cannot be the same.")
        sys.exit(1)
        
    logger.info(f"Source: {args.source}")
    logger.info(f"Target: {args.target}")
    
    try:
        source_engine = create_engine(args.source)
        target_engine = create_engine(args.target)
        
        # Test connections
        with source_engine.connect():
            pass
        with target_engine.connect():
            pass
            
    except Exception as e:
        logger.error(f"Failed to connect to databases: {e}")
        sys.exit(1)
        
    logger.info("NOTE: Please ensure that the target database schema has been initialized via 'alembic upgrade head' before running this script.")
    logger.info("Starting data copy...")
    
    try:
        copy_data(source_engine, target_engine)
        logger.info("Migration completed successfully!")
    except Exception as e:
        logger.exception("Migration failed.")
        sys.exit(1)

if __name__ == "__main__":
    main()
