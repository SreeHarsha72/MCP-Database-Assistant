"""
MCP Server
----------
This server exposes real database tools to the MCP client.
Each tool reads from a real SQLite database.

Important: for stdio-based MCP servers, do not print normal logs to stdout.
stdout is reserved for MCP JSON-RPC messages. Use stderr for logs.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()


_db_env = os.getenv("DATABASE_PATH", "retail.db")
DB_PATH = (Path(_db_env) if Path(_db_env).is_absolute() else Path(__file__).with_name(_db_env)).resolve()

mcp = FastMCP("Retail Analytics DB Server")

ALLOWED_TABLES = {"sales", "inventory", "customers", "inventory_movements", "db_audit_log"}


def log(message: str) -> None:
    print(f"[MCP SERVER] {message}", file=sys.stderr)


def db_connect(readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        # SQLite read-only connection. Good for query tools.
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def to_json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


def clamp_limit(limit: int, default: int = 10, maximum: int = 50) -> int:
    try:
        value = int(limit)
    except Exception:
        value = default
    return max(1, min(value, maximum))


def normalize_optional_text(value: str | None, default: str = "all") -> str:
    if value is None:
        return default
    value = str(value).strip()
    return value if value else default


def safe_table_name(table_name: str) -> str | None:
    table = table_name.strip().lower()
    return table if table in ALLOWED_TABLES else None


def today_iso() -> str:
    return date.today().isoformat()


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def normalize_id(value: str, prefix: str | None = None) -> str:
    cleaned = str(value or "").strip().upper()
    if prefix and cleaned and not cleaned.startswith(prefix):
        cleaned = f"{prefix}{cleaned}"
    return cleaned


def normalize_text_value(value: str, field_name: str, max_len: int = 80) -> tuple[str | None, str | None]:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None, f"{field_name} is required."
    if len(cleaned) > max_len:
        return None, f"{field_name} is too long. Maximum length is {max_len}."
    return cleaned, None


def parse_positive_int(value: int, field_name: str) -> tuple[int | None, str | None]:
    try:
        parsed = int(value)
    except Exception:
        return None, f"{field_name} must be an integer."
    if parsed <= 0:
        return None, f"{field_name} must be greater than 0."
    return parsed, None


def parse_non_negative_int(value: int, field_name: str) -> tuple[int | None, str | None]:
    try:
        parsed = int(value)
    except Exception:
        return None, f"{field_name} must be an integer."
    if parsed < 0:
        return None, f"{field_name} cannot be negative."
    return parsed, None


def parse_money(value: float, field_name: str) -> tuple[float | None, str | None]:
    try:
        parsed = round(float(value), 2)
    except Exception:
        return None, f"{field_name} must be a number."
    if parsed < 0:
        return None, f"{field_name} cannot be negative."
    return parsed, None


def normalize_date(value: str | None) -> tuple[str | None, str | None]:
    cleaned = str(value or "").strip()
    if not cleaned:
        return today_iso(), None
    try:
        datetime.strptime(cleaned, "%Y-%m-%d")
        return cleaned, None
    except ValueError:
        return None, "Date must be in YYYY-MM-DD format."


def log_audit(conn: sqlite3.Connection, action: str, table_name: str, record_key: str, details: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO db_audit_log(action, table_name, record_key, details_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (action, table_name, str(record_key), json.dumps(details, default=str), now_iso()),
    )


def log_inventory_movement(
    conn: sqlite3.Connection,
    product_id: str,
    change_qty: int,
    reason: str,
    reference_id: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO inventory_movements(product_id, change_qty, reason, reference_id, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (product_id, change_qty, reason, reference_id, now_iso()),
    )


def next_id(conn: sqlite3.Connection, table_name: str, id_column: str, prefix: str, step: int = 1) -> str:
    rows = conn.execute(f"SELECT {id_column} AS id_value FROM {table_name} WHERE {id_column} LIKE ?", (f"{prefix}%",)).fetchall()
    max_number = 0
    for row in rows:
        match = re.search(r"(\d+)$", row["id_value"])
        if match:
            max_number = max(max_number, int(match.group(1)))
    next_number = max_number + step
    width = 3 if prefix in {"C", "P"} else 1
    return f"{prefix}{next_number:0{width}d}"


@mcp.tool()
def list_tables() -> str:
    """
    List the database tables available to the MCP server.
    Use this before writing custom SQL if you need to understand the database structure.
    """
    log("list_tables()")
    conn = db_connect(readonly=True)
    try:
        rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            ORDER BY name
            """
        ).fetchall()
        return to_json({"tables": [row["name"] for row in rows]})
    finally:
        conn.close()


@mcp.tool()
def describe_table(table_name: str) -> str:
    """
    Return column names and types for one allowed table.
    Allowed tables: sales, inventory, customers, inventory_movements, db_audit_log.
    """
    table = safe_table_name(table_name)
    log(f"describe_table(table_name={table_name})")
    if table is None:
        return to_json({"error": f"Table '{table_name}' is not allowed. Allowed tables: {sorted(ALLOWED_TABLES)}"})

    conn = db_connect(readonly=True)
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        columns = [
            {
                "column_id": row["cid"],
                "name": row["name"],
                "type": row["type"],
                "not_null": bool(row["notnull"]),
                "primary_key": bool(row["pk"]),
            }
            for row in rows
        ]
        return to_json({"table": table, "columns": columns})
    finally:
        conn.close()


@mcp.tool()
def get_sales_summary(region: str = "all") -> str:
    """
    Return sales summary for a region from the SQLite sales table.
    Use region='all' for company-wide summary.
    """
    region = normalize_optional_text(region).lower()
    log(f"get_sales_summary(region={region})")

    conn = db_connect(readonly=True)
    try:
        if region == "all":
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS order_count,
                    SUM(units) AS total_units,
                    ROUND(SUM(revenue), 2) AS total_revenue,
                    ROUND(AVG(revenue), 2) AS avg_order_revenue,
                    ROUND(SUM(revenue) - SUM(units * i.unit_cost), 2) AS estimated_gross_profit
                FROM sales s
                JOIN inventory i ON s.product_id = i.product_id
                """
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT
                    s.region,
                    COUNT(*) AS order_count,
                    SUM(s.units) AS total_units,
                    ROUND(SUM(s.revenue), 2) AS total_revenue,
                    ROUND(AVG(s.revenue), 2) AS avg_order_revenue,
                    ROUND(SUM(s.revenue) - SUM(s.units * i.unit_cost), 2) AS estimated_gross_profit
                FROM sales s
                JOIN inventory i ON s.product_id = i.product_id
                WHERE LOWER(s.region) = ?
                GROUP BY s.region
                """,
                (region,),
            ).fetchone()

        if row is None or row["order_count"] == 0:
            return to_json({"found": False, "message": f"No sales found for region '{region}'."})

        return to_json({"found": True, "sales_summary": dict(row)})
    finally:
        conn.close()


@mcp.tool()
def get_revenue_by_region() -> str:
    """
    Return revenue, units, order count, and estimated gross profit grouped by region.
    Good for questions like: which region has highest revenue?
    """
    log("get_revenue_by_region()")
    conn = db_connect(readonly=True)
    try:
        rows = conn.execute(
            """
            SELECT
                s.region,
                COUNT(*) AS order_count,
                SUM(s.units) AS total_units,
                ROUND(SUM(s.revenue), 2) AS total_revenue,
                ROUND(SUM(s.revenue) - SUM(s.units * i.unit_cost), 2) AS estimated_gross_profit
            FROM sales s
            JOIN inventory i ON s.product_id = i.product_id
            GROUP BY s.region
            ORDER BY total_revenue DESC
            """
        ).fetchall()
        return to_json({"rows": [dict(row) for row in rows]})
    finally:
        conn.close()


@mcp.tool()
def get_sales_by_channel() -> str:
    """
    Return revenue, units, and order count grouped by sales channel.
    Channels in sample data include online, store, and partner.
    """
    log("get_sales_by_channel()")
    conn = db_connect(readonly=True)
    try:
        rows = conn.execute(
            """
            SELECT
                channel,
                COUNT(*) AS order_count,
                SUM(units) AS total_units,
                ROUND(SUM(revenue), 2) AS total_revenue,
                ROUND(AVG(revenue), 2) AS avg_order_revenue
            FROM sales
            GROUP BY channel
            ORDER BY total_revenue DESC
            """
        ).fetchall()
        return to_json({"rows": [dict(row) for row in rows]})
    finally:
        conn.close()


@mcp.tool()
def get_daily_sales_trend(start_date: str = "", end_date: str = "") -> str:
    """
    Return daily sales trend between optional start_date and end_date.
    Dates should be in YYYY-MM-DD format. Leave blank to include all dates.
    """
    start_date = start_date.strip()
    end_date = end_date.strip()
    log(f"get_daily_sales_trend(start_date={start_date}, end_date={end_date})")

    where_parts: list[str] = []
    params: list[str] = []
    if start_date:
        where_parts.append("order_date >= ?")
        params.append(start_date)
    if end_date:
        where_parts.append("order_date <= ?")
        params.append(end_date)
    where_sql = "WHERE " + " AND ".join(where_parts) if where_parts else ""

    conn = db_connect(readonly=True)
    try:
        rows = conn.execute(
            f"""
            SELECT
                order_date,
                COUNT(*) AS order_count,
                SUM(units) AS total_units,
                ROUND(SUM(revenue), 2) AS total_revenue
            FROM sales
            {where_sql}
            GROUP BY order_date
            ORDER BY order_date
            """,
            params,
        ).fetchall()
        return to_json({"rows": [dict(row) for row in rows]})
    finally:
        conn.close()


@mcp.tool()
def check_inventory(product_id: str) -> str:
    """
    Return inventory status for a product_id from the SQLite inventory table.
    Example product IDs: P100, P200, P300, P400, P500, P600.
    """
    product_id = product_id.strip().upper()
    log(f"check_inventory(product_id={product_id})")

    conn = db_connect(readonly=True)
    try:
        row = conn.execute(
            """
            SELECT
                product_id,
                product_name,
                category,
                stock_qty,
                reorder_level,
                supplier,
                unit_cost,
                unit_price,
                CASE WHEN stock_qty <= reorder_level THEN 1 ELSE 0 END AS reorder_needed
            FROM inventory
            WHERE product_id = ?
            """,
            (product_id,),
        ).fetchone()

        if row is None:
            return to_json({"found": False, "message": f"Product '{product_id}' was not found."})

        result = dict(row)
        result["reorder_needed"] = bool(result["reorder_needed"])
        return to_json({"found": True, "inventory_status": result})
    finally:
        conn.close()


@mcp.tool()
def get_product_details(product_id: str) -> str:
    """
    Return product details plus total units sold, total revenue, and estimated gross profit.
    Good for product performance questions.
    """
    product_id = product_id.strip().upper()
    log(f"get_product_details(product_id={product_id})")

    conn = db_connect(readonly=True)
    try:
        row = conn.execute(
            """
            SELECT
                i.product_id,
                i.product_name,
                i.category,
                i.supplier,
                i.stock_qty,
                i.reorder_level,
                i.unit_cost,
                i.unit_price,
                COALESCE(SUM(s.units), 0) AS total_units_sold,
                ROUND(COALESCE(SUM(s.revenue), 0), 2) AS total_revenue,
                ROUND(COALESCE(SUM(s.revenue), 0) - COALESCE(SUM(s.units * i.unit_cost), 0), 2) AS estimated_gross_profit,
                CASE WHEN i.stock_qty <= i.reorder_level THEN 1 ELSE 0 END AS reorder_needed
            FROM inventory i
            LEFT JOIN sales s ON i.product_id = s.product_id
            WHERE i.product_id = ?
            GROUP BY i.product_id
            """,
            (product_id,),
        ).fetchone()

        if row is None:
            return to_json({"found": False, "message": f"Product '{product_id}' was not found."})

        result = dict(row)
        result["reorder_needed"] = bool(result["reorder_needed"])
        return to_json({"found": True, "product": result})
    finally:
        conn.close()


@mcp.tool()
def search_products(keyword: str) -> str:
    """
    Search products by product name, category, supplier, or product_id.
    Useful when the user does not know the exact product_id.
    """
    keyword = keyword.strip().lower()
    log(f"search_products(keyword={keyword})")
    if not keyword:
        return to_json({"error": "Please provide a keyword."})

    conn = db_connect(readonly=True)
    try:
        rows = conn.execute(
            """
            SELECT product_id, product_name, category, supplier, stock_qty, reorder_level, unit_price
            FROM inventory
            WHERE LOWER(product_id) LIKE ?
               OR LOWER(product_name) LIKE ?
               OR LOWER(category) LIKE ?
               OR LOWER(supplier) LIKE ?
            ORDER BY product_id
            LIMIT 20
            """,
            tuple([f"%{keyword}%"] * 4),
        ).fetchall()
        return to_json({"row_count_returned": len(rows), "rows": [dict(row) for row in rows]})
    finally:
        conn.close()


@mcp.tool()
def get_low_stock_products(category: str = "all") -> str:
    """
    Return products where stock_qty is less than or equal to reorder_level.
    Use category='all' for all categories.
    """
    category = normalize_optional_text(category).lower()
    log(f"get_low_stock_products(category={category})")

    conn = db_connect(readonly=True)
    try:
        if category == "all":
            rows = conn.execute(
                """
                SELECT
                    product_id,
                    product_name,
                    category,
                    supplier,
                    stock_qty,
                    reorder_level,
                    reorder_level - stock_qty AS shortage_qty
                FROM inventory
                WHERE stock_qty <= reorder_level
                ORDER BY shortage_qty DESC, stock_qty ASC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT
                    product_id,
                    product_name,
                    category,
                    supplier,
                    stock_qty,
                    reorder_level,
                    reorder_level - stock_qty AS shortage_qty
                FROM inventory
                WHERE stock_qty <= reorder_level
                  AND LOWER(category) = ?
                ORDER BY shortage_qty DESC, stock_qty ASC
                """,
                (category,),
            ).fetchall()
        return to_json({"row_count_returned": len(rows), "rows": [dict(row) for row in rows]})
    finally:
        conn.close()


@mcp.tool()
def get_supplier_reorder_report(supplier: str = "all") -> str:
    """
    Return reorder report grouped by supplier or filtered to one supplier.
    Good for questions like: which suppliers have products below reorder level?
    """
    supplier = normalize_optional_text(supplier).lower()
    log(f"get_supplier_reorder_report(supplier={supplier})")

    conn = db_connect(readonly=True)
    try:
        if supplier == "all":
            rows = conn.execute(
                """
                SELECT
                    supplier,
                    COUNT(*) AS product_count,
                    SUM(CASE WHEN stock_qty <= reorder_level THEN 1 ELSE 0 END) AS products_needing_reorder,
                    SUM(CASE WHEN stock_qty <= reorder_level THEN reorder_level - stock_qty ELSE 0 END) AS total_shortage_qty
                FROM inventory
                GROUP BY supplier
                ORDER BY products_needing_reorder DESC, total_shortage_qty DESC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT
                    supplier,
                    product_id,
                    product_name,
                    category,
                    stock_qty,
                    reorder_level,
                    CASE WHEN stock_qty <= reorder_level THEN 1 ELSE 0 END AS reorder_needed,
                    CASE WHEN stock_qty <= reorder_level THEN reorder_level - stock_qty ELSE 0 END AS shortage_qty
                FROM inventory
                WHERE LOWER(supplier) = ?
                ORDER BY reorder_needed DESC, shortage_qty DESC
                """,
                (supplier,),
            ).fetchall()

        result_rows = []
        for row in rows:
            result = dict(row)
            if "reorder_needed" in result:
                result["reorder_needed"] = bool(result["reorder_needed"])
            result_rows.append(result)
        return to_json({"row_count_returned": len(result_rows), "rows": result_rows})
    finally:
        conn.close()


@mcp.tool()
def get_top_products_by_revenue(limit: int = 5, region: str = "all") -> str:
    """
    Return top products by revenue, optionally filtered by region.
    Use region='all' for all regions. Limit is capped at 20.
    """
    limit = clamp_limit(limit, default=5, maximum=20)
    region = normalize_optional_text(region).lower()
    log(f"get_top_products_by_revenue(limit={limit}, region={region})")

    conn = db_connect(readonly=True)
    try:
        if region == "all":
            rows = conn.execute(
                """
                SELECT
                    s.product_id,
                    i.product_name,
                    i.category,
                    COUNT(*) AS order_count,
                    SUM(s.units) AS total_units,
                    ROUND(SUM(s.revenue), 2) AS total_revenue,
                    ROUND(SUM(s.revenue) - SUM(s.units * i.unit_cost), 2) AS estimated_gross_profit
                FROM sales s
                JOIN inventory i ON s.product_id = i.product_id
                GROUP BY s.product_id, i.product_name, i.category
                ORDER BY total_revenue DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT
                    s.product_id,
                    i.product_name,
                    i.category,
                    COUNT(*) AS order_count,
                    SUM(s.units) AS total_units,
                    ROUND(SUM(s.revenue), 2) AS total_revenue,
                    ROUND(SUM(s.revenue) - SUM(s.units * i.unit_cost), 2) AS estimated_gross_profit
                FROM sales s
                JOIN inventory i ON s.product_id = i.product_id
                WHERE LOWER(s.region) = ?
                GROUP BY s.product_id, i.product_name, i.category
                ORDER BY total_revenue DESC
                LIMIT ?
                """,
                (region, limit),
            ).fetchall()
        return to_json({"row_count_returned": len(rows), "rows": [dict(row) for row in rows]})
    finally:
        conn.close()


@mcp.tool()
def get_customer_profile(customer_id: str) -> str:
    """
    Return customer profile plus purchase summary.
    Example customer IDs: C001, C002, C003, C004, C005, C006.
    """
    customer_id = customer_id.strip().upper()
    log(f"get_customer_profile(customer_id={customer_id})")

    conn = db_connect(readonly=True)
    try:
        row = conn.execute(
            """
            SELECT
                c.customer_id,
                c.customer_name,
                c.segment,
                c.region,
                c.signup_date,
                COUNT(s.order_id) AS order_count,
                COALESCE(SUM(s.units), 0) AS total_units,
                ROUND(COALESCE(SUM(s.revenue), 0), 2) AS total_revenue,
                MAX(s.order_date) AS last_order_date
            FROM customers c
            LEFT JOIN sales s ON c.customer_id = s.customer_id
            WHERE c.customer_id = ?
            GROUP BY c.customer_id
            """,
            (customer_id,),
        ).fetchone()

        if row is None:
            return to_json({"found": False, "message": f"Customer '{customer_id}' was not found."})
        return to_json({"found": True, "customer_profile": dict(row)})
    finally:
        conn.close()


@mcp.tool()
def get_customer_orders(customer_id: str, limit: int = 10) -> str:
    """
    Return recent orders for one customer. Limit is capped at 25.
    """
    customer_id = customer_id.strip().upper()
    limit = clamp_limit(limit, default=10, maximum=25)
    log(f"get_customer_orders(customer_id={customer_id}, limit={limit})")

    conn = db_connect(readonly=True)
    try:
        rows = conn.execute(
            """
            SELECT
                s.order_id,
                s.order_date,
                s.customer_id,
                c.customer_name,
                s.region,
                s.product_id,
                i.product_name,
                s.units,
                s.revenue,
                s.channel
            FROM sales s
            JOIN customers c ON s.customer_id = c.customer_id
            JOIN inventory i ON s.product_id = i.product_id
            WHERE s.customer_id = ?
            ORDER BY s.order_date DESC, s.order_id DESC
            LIMIT ?
            """,
            (customer_id, limit),
        ).fetchall()
        return to_json({"row_count_returned": len(rows), "rows": [dict(row) for row in rows]})
    finally:
        conn.close()


@mcp.tool()
def get_order_details(order_id: int) -> str:
    """
    Return full details for one order_id, joining sales, customers, and inventory.
    """
    log(f"get_order_details(order_id={order_id})")
    try:
        order_id_int = int(order_id)
    except Exception:
        return to_json({"error": "order_id must be an integer."})

    conn = db_connect(readonly=True)
    try:
        row = conn.execute(
            """
            SELECT
                s.order_id,
                s.order_date,
                s.region,
                s.channel,
                s.customer_id,
                c.customer_name,
                c.segment,
                s.product_id,
                i.product_name,
                i.category,
                s.units,
                s.revenue,
                i.unit_cost,
                i.unit_price,
                ROUND(s.revenue - (s.units * i.unit_cost), 2) AS estimated_gross_profit
            FROM sales s
            JOIN customers c ON s.customer_id = c.customer_id
            JOIN inventory i ON s.product_id = i.product_id
            WHERE s.order_id = ?
            """,
            (order_id_int,),
        ).fetchone()

        if row is None:
            return to_json({"found": False, "message": f"Order '{order_id}' was not found."})
        return to_json({"found": True, "order": dict(row)})
    finally:
        conn.close()


@mcp.tool()
def get_segment_performance() -> str:
    """
    Return sales performance by customer segment.
    Good for questions about B2B vs Consumer vs Healthcare/Education performance.
    """
    log("get_segment_performance()")
    conn = db_connect(readonly=True)
    try:
        rows = conn.execute(
            """
            SELECT
                c.segment,
                COUNT(s.order_id) AS order_count,
                COUNT(DISTINCT c.customer_id) AS customer_count,
                SUM(s.units) AS total_units,
                ROUND(SUM(s.revenue), 2) AS total_revenue,
                ROUND(AVG(s.revenue), 2) AS avg_order_revenue
            FROM sales s
            JOIN customers c ON s.customer_id = c.customer_id
            GROUP BY c.segment
            ORDER BY total_revenue DESC
            """
        ).fetchall()
        return to_json({"rows": [dict(row) for row in rows]})
    finally:
        conn.close()



@mcp.tool()
def create_customer(
    customer_name: str,
    segment: str,
    region: str,
    customer_id: str = "",
    signup_date: str = "",
) -> str:
    """
    Write operation: create a new customer row in the customers table.
    If customer_id is blank, the server generates the next ID like C007.
    signup_date defaults to today's date when blank.
    """
    log(f"create_customer(customer_name={customer_name}, segment={segment}, region={region}, customer_id={customer_id})")

    customer_name_clean, error = normalize_text_value(customer_name, "customer_name")
    if error:
        return to_json({"success": False, "error": error})
    segment_clean, error = normalize_text_value(segment, "segment", max_len=40)
    if error:
        return to_json({"success": False, "error": error})
    region_clean, error = normalize_text_value(region, "region", max_len=30)
    if error:
        return to_json({"success": False, "error": error})
    signup_date_clean, error = normalize_date(signup_date)
    if error:
        return to_json({"success": False, "error": error})

    customer_id_clean = normalize_id(customer_id, prefix="C") if customer_id.strip() else ""

    conn = db_connect(readonly=False)
    try:
        with conn:
            if not customer_id_clean:
                customer_id_clean = next_id(conn, "customers", "customer_id", "C", step=1)

            existing = conn.execute(
                "SELECT customer_id FROM customers WHERE customer_id = ?",
                (customer_id_clean,),
            ).fetchone()
            if existing:
                return to_json({"success": False, "error": f"Customer {customer_id_clean} already exists."})

            conn.execute(
                """
                INSERT INTO customers(customer_id, customer_name, segment, region, signup_date)
                VALUES (?, ?, ?, ?, ?)
                """,
                (customer_id_clean, customer_name_clean, segment_clean, region_clean.lower(), signup_date_clean),
            )
            log_audit(
                conn,
                action="create_customer",
                table_name="customers",
                record_key=customer_id_clean,
                details={
                    "customer_id": customer_id_clean,
                    "customer_name": customer_name_clean,
                    "segment": segment_clean,
                    "region": region_clean.lower(),
                    "signup_date": signup_date_clean,
                },
            )
        return to_json({"success": True, "customer_id": customer_id_clean, "message": "Customer created."})
    except Exception as exc:
        return to_json({"success": False, "error": str(exc)})
    finally:
        conn.close()


@mcp.tool()
def update_customer_segment(customer_id: str, segment: str) -> str:
    """
    Write operation: update the segment for an existing customer.
    """
    customer_id_clean = normalize_id(customer_id, prefix="C")
    segment_clean, error = normalize_text_value(segment, "segment", max_len=40)
    log(f"update_customer_segment(customer_id={customer_id_clean}, segment={segment})")
    if error:
        return to_json({"success": False, "error": error})

    conn = db_connect(readonly=False)
    try:
        with conn:
            row = conn.execute(
                "SELECT customer_id, segment FROM customers WHERE customer_id = ?",
                (customer_id_clean,),
            ).fetchone()
            if row is None:
                return to_json({"success": False, "error": f"Customer {customer_id_clean} was not found."})

            old_segment = row["segment"]
            conn.execute(
                "UPDATE customers SET segment = ? WHERE customer_id = ?",
                (segment_clean, customer_id_clean),
            )
            log_audit(
                conn,
                action="update_customer_segment",
                table_name="customers",
                record_key=customer_id_clean,
                details={"old_segment": old_segment, "new_segment": segment_clean},
            )
        return to_json({"success": True, "customer_id": customer_id_clean, "old_segment": old_segment, "new_segment": segment_clean})
    except Exception as exc:
        return to_json({"success": False, "error": str(exc)})
    finally:
        conn.close()


@mcp.tool()
def create_product(
    product_name: str,
    category: str,
    stock_qty: int,
    reorder_level: int,
    supplier: str,
    unit_cost: float,
    unit_price: float,
    product_id: str = "",
) -> str:
    """
    Write operation: create a new product row in the inventory table.
    If product_id is blank, the server generates the next ID like P601 or P700 depending on existing IDs.
    """
    log(f"create_product(product_name={product_name}, product_id={product_id})")

    product_name_clean, error = normalize_text_value(product_name, "product_name")
    if error:
        return to_json({"success": False, "error": error})
    category_clean, error = normalize_text_value(category, "category", max_len=50)
    if error:
        return to_json({"success": False, "error": error})
    supplier_clean, error = normalize_text_value(supplier, "supplier", max_len=60)
    if error:
        return to_json({"success": False, "error": error})
    stock_qty_int, error = parse_non_negative_int(stock_qty, "stock_qty")
    if error:
        return to_json({"success": False, "error": error})
    reorder_level_int, error = parse_non_negative_int(reorder_level, "reorder_level")
    if error:
        return to_json({"success": False, "error": error})
    unit_cost_float, error = parse_money(unit_cost, "unit_cost")
    if error:
        return to_json({"success": False, "error": error})
    unit_price_float, error = parse_money(unit_price, "unit_price")
    if error:
        return to_json({"success": False, "error": error})

    product_id_clean = normalize_id(product_id, prefix="P") if product_id.strip() else ""

    conn = db_connect(readonly=False)
    try:
        with conn:
            if not product_id_clean:
                product_id_clean = next_id(conn, "inventory", "product_id", "P", step=100)

            existing = conn.execute(
                "SELECT product_id FROM inventory WHERE product_id = ?",
                (product_id_clean,),
            ).fetchone()
            if existing:
                return to_json({"success": False, "error": f"Product {product_id_clean} already exists."})

            conn.execute(
                """
                INSERT INTO inventory(product_id, product_name, category, stock_qty, reorder_level, supplier, unit_cost, unit_price)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product_id_clean,
                    product_name_clean,
                    category_clean,
                    stock_qty_int,
                    reorder_level_int,
                    supplier_clean,
                    unit_cost_float,
                    unit_price_float,
                ),
            )
            if stock_qty_int > 0:
                log_inventory_movement(
                    conn,
                    product_id=product_id_clean,
                    change_qty=stock_qty_int,
                    reason="initial_product_stock",
                    reference_id=f"product:{product_id_clean}",
                )
            log_audit(
                conn,
                action="create_product",
                table_name="inventory",
                record_key=product_id_clean,
                details={
                    "product_id": product_id_clean,
                    "product_name": product_name_clean,
                    "category": category_clean,
                    "stock_qty": stock_qty_int,
                    "reorder_level": reorder_level_int,
                    "supplier": supplier_clean,
                    "unit_cost": unit_cost_float,
                    "unit_price": unit_price_float,
                },
            )
        return to_json({"success": True, "product_id": product_id_clean, "message": "Product created."})
    except Exception as exc:
        return to_json({"success": False, "error": str(exc)})
    finally:
        conn.close()


@mcp.tool()
def update_product_price(product_id: str, unit_price: float) -> str:
    """
    Write operation: update a product's selling price in the inventory table.
    """
    product_id_clean = normalize_id(product_id, prefix="P")
    unit_price_float, error = parse_money(unit_price, "unit_price")
    log(f"update_product_price(product_id={product_id_clean}, unit_price={unit_price})")
    if error:
        return to_json({"success": False, "error": error})

    conn = db_connect(readonly=False)
    try:
        with conn:
            row = conn.execute(
                "SELECT product_id, unit_price FROM inventory WHERE product_id = ?",
                (product_id_clean,),
            ).fetchone()
            if row is None:
                return to_json({"success": False, "error": f"Product {product_id_clean} was not found."})
            old_price = row["unit_price"]
            conn.execute(
                "UPDATE inventory SET unit_price = ? WHERE product_id = ?",
                (unit_price_float, product_id_clean),
            )
            log_audit(
                conn,
                action="update_product_price",
                table_name="inventory",
                record_key=product_id_clean,
                details={"old_unit_price": old_price, "new_unit_price": unit_price_float},
            )
        return to_json({"success": True, "product_id": product_id_clean, "old_unit_price": old_price, "new_unit_price": unit_price_float})
    except Exception as exc:
        return to_json({"success": False, "error": str(exc)})
    finally:
        conn.close()


@mcp.tool()
def update_reorder_level(product_id: str, reorder_level: int) -> str:
    """
    Write operation: update the reorder_level threshold for a product.
    """
    product_id_clean = normalize_id(product_id, prefix="P")
    reorder_level_int, error = parse_non_negative_int(reorder_level, "reorder_level")
    log(f"update_reorder_level(product_id={product_id_clean}, reorder_level={reorder_level})")
    if error:
        return to_json({"success": False, "error": error})

    conn = db_connect(readonly=False)
    try:
        with conn:
            row = conn.execute(
                "SELECT product_id, reorder_level FROM inventory WHERE product_id = ?",
                (product_id_clean,),
            ).fetchone()
            if row is None:
                return to_json({"success": False, "error": f"Product {product_id_clean} was not found."})
            old_level = row["reorder_level"]
            conn.execute(
                "UPDATE inventory SET reorder_level = ? WHERE product_id = ?",
                (reorder_level_int, product_id_clean),
            )
            log_audit(
                conn,
                action="update_reorder_level",
                table_name="inventory",
                record_key=product_id_clean,
                details={"old_reorder_level": old_level, "new_reorder_level": reorder_level_int},
            )
        return to_json({"success": True, "product_id": product_id_clean, "old_reorder_level": old_level, "new_reorder_level": reorder_level_int})
    except Exception as exc:
        return to_json({"success": False, "error": str(exc)})
    finally:
        conn.close()


@mcp.tool()
def restock_inventory(product_id: str, quantity: int, note: str = "") -> str:
    """
    Write operation: add stock to a product.
    This updates inventory.stock_qty and inserts an inventory movement record.
    """
    product_id_clean = normalize_id(product_id, prefix="P")
    quantity_int, error = parse_positive_int(quantity, "quantity")
    note_clean = str(note or "").strip()[:120]
    log(f"restock_inventory(product_id={product_id_clean}, quantity={quantity})")
    if error:
        return to_json({"success": False, "error": error})

    conn = db_connect(readonly=False)
    try:
        with conn:
            row = conn.execute(
                "SELECT product_id, stock_qty FROM inventory WHERE product_id = ?",
                (product_id_clean,),
            ).fetchone()
            if row is None:
                return to_json({"success": False, "error": f"Product {product_id_clean} was not found."})
            old_qty = row["stock_qty"]
            new_qty = old_qty + quantity_int
            conn.execute(
                "UPDATE inventory SET stock_qty = ? WHERE product_id = ?",
                (new_qty, product_id_clean),
            )
            log_inventory_movement(
                conn,
                product_id=product_id_clean,
                change_qty=quantity_int,
                reason="restock" if not note_clean else f"restock: {note_clean}",
                reference_id="manual_restock",
            )
            log_audit(
                conn,
                action="restock_inventory",
                table_name="inventory",
                record_key=product_id_clean,
                details={"old_stock_qty": old_qty, "change_qty": quantity_int, "new_stock_qty": new_qty, "note": note_clean},
            )
        return to_json({"success": True, "product_id": product_id_clean, "old_stock_qty": old_qty, "change_qty": quantity_int, "new_stock_qty": new_qty})
    except Exception as exc:
        return to_json({"success": False, "error": str(exc)})
    finally:
        conn.close()


@mcp.tool()
def adjust_inventory(product_id: str, change_qty: int, reason: str) -> str:
    """
    Write operation: manually adjust product stock up or down.
    Positive change_qty increases stock. Negative change_qty decreases stock.
    The server blocks changes that would make stock negative.
    """
    product_id_clean = normalize_id(product_id, prefix="P")
    try:
        change_qty_int = int(change_qty)
    except Exception:
        return to_json({"success": False, "error": "change_qty must be an integer."})
    if change_qty_int == 0:
        return to_json({"success": False, "error": "change_qty cannot be 0."})
    reason_clean, error = normalize_text_value(reason, "reason", max_len=120)
    log(f"adjust_inventory(product_id={product_id_clean}, change_qty={change_qty})")
    if error:
        return to_json({"success": False, "error": error})

    conn = db_connect(readonly=False)
    try:
        with conn:
            row = conn.execute(
                "SELECT product_id, stock_qty FROM inventory WHERE product_id = ?",
                (product_id_clean,),
            ).fetchone()
            if row is None:
                return to_json({"success": False, "error": f"Product {product_id_clean} was not found."})
            old_qty = row["stock_qty"]
            new_qty = old_qty + change_qty_int
            if new_qty < 0:
                return to_json({"success": False, "error": f"Adjustment would make stock negative. Current stock is {old_qty}."})
            conn.execute(
                "UPDATE inventory SET stock_qty = ? WHERE product_id = ?",
                (new_qty, product_id_clean),
            )
            log_inventory_movement(
                conn,
                product_id=product_id_clean,
                change_qty=change_qty_int,
                reason=f"manual_adjustment: {reason_clean}",
                reference_id="manual_adjustment",
            )
            log_audit(
                conn,
                action="adjust_inventory",
                table_name="inventory",
                record_key=product_id_clean,
                details={"old_stock_qty": old_qty, "change_qty": change_qty_int, "new_stock_qty": new_qty, "reason": reason_clean},
            )
        return to_json({"success": True, "product_id": product_id_clean, "old_stock_qty": old_qty, "change_qty": change_qty_int, "new_stock_qty": new_qty})
    except Exception as exc:
        return to_json({"success": False, "error": str(exc)})
    finally:
        conn.close()


@mcp.tool()
def create_sales_order(
    customer_id: str,
    product_id: str,
    units: int,
    channel: str = "online",
    order_date: str = "",
    region: str = "",
) -> str:
    """
    Write operation: create a sales order.
    This inserts into sales, decrements inventory.stock_qty, and writes audit/movement logs in one transaction.
    Revenue is calculated from units * current inventory.unit_price.
    If region is blank, the customer's region is used.
    """
    customer_id_clean = normalize_id(customer_id, prefix="C")
    product_id_clean = normalize_id(product_id, prefix="P")
    units_int, error = parse_positive_int(units, "units")
    log(f"create_sales_order(customer_id={customer_id_clean}, product_id={product_id_clean}, units={units})")
    if error:
        return to_json({"success": False, "error": error})
    order_date_clean, error = normalize_date(order_date)
    if error:
        return to_json({"success": False, "error": error})
    channel_clean = str(channel or "online").strip().lower()
    if channel_clean not in {"online", "store", "partner"}:
        return to_json({"success": False, "error": "channel must be one of: online, store, partner."})

    conn = db_connect(readonly=False)
    try:
        with conn:
            customer = conn.execute(
                "SELECT customer_id, region FROM customers WHERE customer_id = ?",
                (customer_id_clean,),
            ).fetchone()
            if customer is None:
                return to_json({"success": False, "error": f"Customer {customer_id_clean} was not found."})

            product = conn.execute(
                "SELECT product_id, product_name, stock_qty, unit_price FROM inventory WHERE product_id = ?",
                (product_id_clean,),
            ).fetchone()
            if product is None:
                return to_json({"success": False, "error": f"Product {product_id_clean} was not found."})

            current_stock = int(product["stock_qty"])
            if current_stock < units_int:
                return to_json(
                    {
                        "success": False,
                        "error": f"Not enough stock for {product_id_clean}. Current stock is {current_stock}, requested units is {units_int}.",
                    }
                )

            order_region = str(region or customer["region"]).strip().lower()
            revenue = round(units_int * float(product["unit_price"]), 2)
            cur = conn.execute(
                """
                INSERT INTO sales(order_date, customer_id, region, product_id, units, revenue, channel)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (order_date_clean, customer_id_clean, order_region, product_id_clean, units_int, revenue, channel_clean),
            )
            order_id = int(cur.lastrowid)
            new_stock = current_stock - units_int
            conn.execute(
                "UPDATE inventory SET stock_qty = ? WHERE product_id = ?",
                (new_stock, product_id_clean),
            )
            log_inventory_movement(
                conn,
                product_id=product_id_clean,
                change_qty=-units_int,
                reason="sales_order_created",
                reference_id=f"order:{order_id}",
            )
            log_audit(
                conn,
                action="create_sales_order",
                table_name="sales",
                record_key=str(order_id),
                details={
                    "order_id": order_id,
                    "order_date": order_date_clean,
                    "customer_id": customer_id_clean,
                    "product_id": product_id_clean,
                    "units": units_int,
                    "revenue": revenue,
                    "channel": channel_clean,
                    "old_stock_qty": current_stock,
                    "new_stock_qty": new_stock,
                },
            )
        return to_json(
            {
                "success": True,
                "order_id": order_id,
                "customer_id": customer_id_clean,
                "product_id": product_id_clean,
                "product_name": product["product_name"],
                "units": units_int,
                "revenue": revenue,
                "old_stock_qty": current_stock,
                "new_stock_qty": new_stock,
                "message": "Sales order created and inventory decremented.",
            }
        )
    except Exception as exc:
        return to_json({"success": False, "error": str(exc)})
    finally:
        conn.close()


@mcp.tool()
def cancel_order(order_id: int, reason: str = "") -> str:
    """
    Write operation: cancel an order by deleting it from sales and restocking its units.
    This is transactional and writes audit/movement logs.
    """
    try:
        order_id_int = int(order_id)
    except Exception:
        return to_json({"success": False, "error": "order_id must be an integer."})
    reason_clean = str(reason or "").strip()[:120]
    log(f"cancel_order(order_id={order_id_int})")

    conn = db_connect(readonly=False)
    try:
        with conn:
            order = conn.execute(
                "SELECT order_id, order_date, customer_id, product_id, units, revenue, channel FROM sales WHERE order_id = ?",
                (order_id_int,),
            ).fetchone()
            if order is None:
                return to_json({"success": False, "error": f"Order {order_id_int} was not found."})

            product_id_clean = order["product_id"]
            units_int = int(order["units"])
            stock_row = conn.execute(
                "SELECT stock_qty FROM inventory WHERE product_id = ?",
                (product_id_clean,),
            ).fetchone()
            old_stock = int(stock_row["stock_qty"]) if stock_row else 0
            new_stock = old_stock + units_int

            conn.execute("DELETE FROM sales WHERE order_id = ?", (order_id_int,))
            conn.execute(
                "UPDATE inventory SET stock_qty = ? WHERE product_id = ?",
                (new_stock, product_id_clean),
            )
            log_inventory_movement(
                conn,
                product_id=product_id_clean,
                change_qty=units_int,
                reason="order_cancelled" if not reason_clean else f"order_cancelled: {reason_clean}",
                reference_id=f"cancelled_order:{order_id_int}",
            )
            log_audit(
                conn,
                action="cancel_order",
                table_name="sales",
                record_key=str(order_id_int),
                details={
                    "cancelled_order": dict(order),
                    "reason": reason_clean,
                    "old_stock_qty": old_stock,
                    "new_stock_qty": new_stock,
                },
            )
        return to_json(
            {
                "success": True,
                "order_id": order_id_int,
                "product_id": product_id_clean,
                "restocked_units": units_int,
                "old_stock_qty": old_stock,
                "new_stock_qty": new_stock,
                "message": "Order cancelled, sales row deleted, and inventory restocked.",
            }
        )
    except Exception as exc:
        return to_json({"success": False, "error": str(exc)})
    finally:
        conn.close()


@mcp.tool()
def get_inventory_movements(product_id: str = "", limit: int = 20) -> str:
    """
    Read verification tool: return inventory write history/movements.
    Useful after restock, adjustment, order creation, or cancellation.
    """
    product_id_clean = normalize_id(product_id, prefix="P") if product_id.strip() else ""
    limit_int = clamp_limit(limit, default=20, maximum=100)
    log(f"get_inventory_movements(product_id={product_id_clean}, limit={limit_int})")

    conn = db_connect(readonly=True)
    try:
        if product_id_clean:
            rows = conn.execute(
                """
                SELECT movement_id, product_id, change_qty, reason, reference_id, created_at
                FROM inventory_movements
                WHERE product_id = ?
                ORDER BY movement_id DESC
                LIMIT ?
                """,
                (product_id_clean, limit_int),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT movement_id, product_id, change_qty, reason, reference_id, created_at
                FROM inventory_movements
                ORDER BY movement_id DESC
                LIMIT ?
                """,
                (limit_int,),
            ).fetchall()
        return to_json({"row_count_returned": len(rows), "rows": [dict(row) for row in rows]})
    finally:
        conn.close()


@mcp.tool()
def get_audit_log(limit: int = 20) -> str:
    """
    Read verification tool: return recent write operations recorded by the MCP server.
    """
    limit_int = clamp_limit(limit, default=20, maximum=100)
    log(f"get_audit_log(limit={limit_int})")
    conn = db_connect(readonly=True)
    try:
        rows = conn.execute(
            """
            SELECT audit_id, action, table_name, record_key, details_json, created_at
            FROM db_audit_log
            ORDER BY audit_id DESC
            LIMIT ?
            """,
            (limit_int,),
        ).fetchall()
        return to_json({"row_count_returned": len(rows), "rows": [dict(row) for row in rows]})
    finally:
        conn.close()


@mcp.tool()
def run_readonly_sql(query: str) -> str:
    """
    Run a read-only SELECT query against the SQLite database.
    Available tables:
    - sales(order_id, order_date, customer_id, region, product_id, units, revenue, channel)
    - inventory(product_id, product_name, category, stock_qty, reorder_level, supplier, unit_cost, unit_price)
    - customers(customer_id, customer_name, segment, region, signup_date)
    - inventory_movements(movement_id, product_id, change_qty, reason, reference_id, created_at)
    - db_audit_log(audit_id, action, table_name, record_key, details_json, created_at)

    Safety rules:
    - Only SELECT or WITH queries are allowed.
    - Multiple SQL statements are not allowed.
    - Maximum 50 rows are returned.
    - Use this only when the specific tools above cannot answer the question.
    """
    cleaned = query.strip()
    log(f"run_readonly_sql(query={cleaned!r})")

    lowered = cleaned.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        return to_json({"error": "Only SELECT or WITH read-only queries are allowed."})

    # Basic multiple-statement guard. sqlite3.execute blocks multiple statements too,
    # but this makes the intent clear for learning.
    if ";" in cleaned.rstrip(";"):
        return to_json({"error": "Multiple SQL statements are not allowed."})

    blocked_keywords = [
        "insert",
        "update",
        "delete",
        "drop",
        "alter",
        "create",
        "replace",
        "attach",
        "detach",
        "pragma",
        "vacuum",
    ]
    padded = f" {lowered} "
    if any(f" {keyword} " in padded for keyword in blocked_keywords):
        return to_json({"error": "Query contains a blocked keyword. Only read-only analytics queries are allowed."})

    conn = db_connect(readonly=True)
    try:
        cur = conn.execute(cleaned)
        rows = [dict(row) for row in cur.fetchmany(50)]
        columns = [description[0] for description in cur.description or []]
        return to_json({"columns": columns, "row_count_returned": len(rows), "rows": rows})
    except Exception as exc:  # Return DB error to LLM as text, don't crash server.
        return to_json({"error": str(exc)})
    finally:
        conn.close()


if __name__ == "__main__":
    if not DB_PATH.exists():
        log(f"Database not found at {DB_PATH}. Run: python init_db.py")
    mcp.run(transport="stdio")
