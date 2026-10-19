# SPanel MySQL / MariaDB Migration Guide

SPanel runs on SQLite by default. For large-scale servers with thousands of accounts, migrating to MySQL or MariaDB is highly recommended to prevent database lock bottlenecks.

## Prerequisites
- A running MySQL or MariaDB server.
- A database created for SPanel (e.g., `spanel`).
- The Python driver is already installed (`pymysql`).

## Migration Steps

1. **Stop the SPanel API service** so no new data is written to SQLite:
   ```bash
   systemctl stop spanel-api
   ```

2. **Initialize the Schema on MySQL**
   Run the Alembic migrations against your new MySQL database so the schema is created properly:
   ```bash
   cd /opt/spanel/backend
   source .venv/bin/activate
   pip install pymysql
   export DATABASE_URL="mysql+pymysql://spanel_db_user:ApOVKhpWMovfb9tCN1g2tcFwKMoFoP@localhost/spanel"
   alembic upgrade head
   ```

3. **Run the Migration Script**
   Run the provided `migrate_db.py` script to copy all existing data from SQLite to MySQL:
   ```bash
   python migrate_db.py --mysql "mysql+pymysql://spanel_db_user:ApOVKhpWMovfb9tCN1g2tcFwKMoFoP@localhost/spanel"
   ```
   *(Ensure you replace `user:password@localhost/spanel` with your actual MySQL credentials)*

4. **Update the Environment Config**
   Edit the `.env` file for SPanel to use the new database.
   ```bash
   nano /opt/spanel/backend/.env
   ```
   Find the `DATABASE_URL` line and change it to your MySQL connection string:
   ```env
   DATABASE_URL=mysql+pymysql://spanel_db_user:ApOVKhpWMovfb9tCN1g2tcFwKMoFoP@localhost/spanel
   ```

5. **Restart the Service**
   Reload systemd and start the backend:
   ```bash
   systemctl daemon-reload
   systemctl start spanel-api
   ```

SPanel will now run entirely on MySQL/MariaDB!
