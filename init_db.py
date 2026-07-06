"""
Create a small real SQLite database for the MCP server to query.
Run this once before starting the host app:

    python init_db.py
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


_db_env = os.getenv("DATABASE_PATH", "retail.db")
DB_PATH = (Path(_db_env) if Path(_db_env).is_absolute() else Path(__file__).with_name(_db_env)).resolve()


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.executescript(
        """
        DROP TABLE IF EXISTS inventory_movements;
        DROP TABLE IF EXISTS db_audit_log;
        DROP TABLE IF EXISTS sales;
        DROP TABLE IF EXISTS inventory;
        DROP TABLE IF EXISTS customers;

        CREATE TABLE customers (
            customer_id TEXT PRIMARY KEY,
            customer_name TEXT NOT NULL,
            segment TEXT NOT NULL,
            region TEXT NOT NULL,
            signup_date TEXT NOT NULL
        );

        CREATE TABLE inventory (
            product_id TEXT PRIMARY KEY,
            product_name TEXT NOT NULL,
            category TEXT NOT NULL,
            stock_qty INTEGER NOT NULL,
            reorder_level INTEGER NOT NULL,
            supplier TEXT NOT NULL,
            unit_cost REAL NOT NULL,
            unit_price REAL NOT NULL
        );

        CREATE TABLE sales (
            order_id INTEGER PRIMARY KEY,
            order_date TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            region TEXT NOT NULL,
            product_id TEXT NOT NULL,
            units INTEGER NOT NULL,
            revenue REAL NOT NULL,
            channel TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
            FOREIGN KEY (product_id) REFERENCES inventory(product_id)
        );

        CREATE TABLE inventory_movements (
            movement_id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT NOT NULL,
            change_qty INTEGER NOT NULL,
            reason TEXT NOT NULL,
            reference_id TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (product_id) REFERENCES inventory(product_id)
        );

        CREATE TABLE db_audit_log (
            audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            table_name TEXT NOT NULL,
            record_key TEXT NOT NULL,
            details_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )

    customers_rows = [
        ("C001", "Apex Consulting", "B2B", "west", "2025-11-10"),
        ("C002", "Green Valley School", "Education", "east", "2025-12-02"),
        ("C003", "HomePro Supplies", "B2B", "south", "2026-01-16"),
        ("C004", "Maya Patel", "Consumer", "west", "2026-02-21"),
        ("C005", "NorthStar Clinic", "Healthcare", "north", "2026-03-05"),
        ("C006", "James Carter", "Consumer", "east", "2026-03-18"),
    ]

    inventory_rows = [
        ("P100", "Wireless Keyboard", "Electronics", 48, 15, "LogiSupply", 58.00, 120.00),
        ("P200", "USB-C Hub", "Electronics", 8, 12, "CableWorks", 31.00, 80.00),
        ("P300", "Desk Lamp", "Home Office", 31, 10, "BrightCo", 28.00, 65.00),
        ("P400", "Ergonomic Mouse", "Electronics", 5, 10, "LogiSupply", 54.00, 150.00),
        ("P500", "Laptop Stand", "Home Office", 18, 8, "StandRight", 22.00, 55.00),
        ("P600", "Noise Cancelling Headset", "Electronics", 6, 9, "SoundPro", 72.00, 180.00),
    ]

    sales_rows = [
        (1, "2026-06-01", "C001", "west", "P100", 10, 1200.00, "online"),
        (2, "2026-06-02", "C004", "west", "P200", 4, 320.00, "online"),
        (3, "2026-06-02", "C002", "east", "P100", 7, 840.00, "store"),
        (4, "2026-06-03", "C003", "south", "P300", 14, 910.00, "partner"),
        (5, "2026-06-04", "C004", "west", "P300", 6, 390.00, "online"),
        (6, "2026-06-04", "C006", "east", "P200", 9, 720.00, "online"),
        (7, "2026-06-05", "C005", "north", "P400", 3, 450.00, "store"),
        (8, "2026-06-05", "C001", "west", "P400", 5, 750.00, "partner"),
        (9, "2026-06-06", "C003", "south", "P100", 8, 960.00, "partner"),
        (10, "2026-06-06", "C002", "east", "P300", 11, 715.00, "store"),
        (11, "2026-06-07", "C001", "west", "P500", 12, 660.00, "online"),
        (12, "2026-06-07", "C005", "north", "P600", 2, 360.00, "store"),
        (13, "2026-06-08", "C006", "east", "P500", 5, 275.00, "online"),
        (14, "2026-06-08", "C004", "west", "P600", 1, 180.00, "online"),
        (15, "2026-06-09", "C003", "south", "P400", 4, 600.00, "partner"),
    ]

    cur.executemany(
        "INSERT INTO customers(customer_id, customer_name, segment, region, signup_date) VALUES (?, ?, ?, ?, ?)",
        customers_rows,
    )
    cur.executemany(
        """
        INSERT INTO inventory(product_id, product_name, category, stock_qty, reorder_level, supplier, unit_cost, unit_price)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        inventory_rows,
    )
    cur.executemany(
        """
        INSERT INTO sales(order_id, order_date, customer_id, region, product_id, units, revenue, channel)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        sales_rows,
    )

    movement_rows = [
        ("P100", 48, "initial_seed_stock", "seed", "2026-06-01T08:00:00"),
        ("P200", 8, "initial_seed_stock", "seed", "2026-06-01T08:00:00"),
        ("P300", 31, "initial_seed_stock", "seed", "2026-06-01T08:00:00"),
        ("P400", 5, "initial_seed_stock", "seed", "2026-06-01T08:00:00"),
        ("P500", 18, "initial_seed_stock", "seed", "2026-06-01T08:00:00"),
        ("P600", 6, "initial_seed_stock", "seed", "2026-06-01T08:00:00"),
    ]

    cur.executemany(
        """
        INSERT INTO inventory_movements(product_id, change_qty, reason, reference_id, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        movement_rows,
    )

    cur.execute(
        """
        INSERT INTO db_audit_log(action, table_name, record_key, details_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "seed_database",
            "database",
            "retail.db",
            '{"message": "Initial demo data loaded for MCP write-operation project."}',
            "2026-06-01T08:00:00",
        ),
    )

    conn.commit()
    conn.close()
    print(f"Created SQLite database with write-operation demo tables at: {DB_PATH}")


if __name__ == "__main__":
    main()
