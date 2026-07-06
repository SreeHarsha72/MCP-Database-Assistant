# MCP-Database-Assistant


The goal of this project is to build a simple realistic Database AI assistant following  Model-Context-Protocol (MCP) standard that can understand natural-language questions using LLM like Ollama, communicates with database tools through an MCP client-server architecture, selects the correct database operation, and performs safe read/write actions on an external SQLite database.

## Project Structure:

```text
mcp_db_assistant/
├── streamlit_app.py   # Simple browser UI for dropdown/custom questions, write approval, and final answer
├── host_app.py        # Host app: Ollama LLM + MCP client orchestration
├── mcp_server.py      # MCP server: exposes read/write DB tools
├── init_db.py         # Creates and resets SQLite database
├── retail.db          # Sample SQLite database
├── requirements.txt
├── .env
```
**Tech stack used:**  Python, Ollama-qwen2.5:7b, MCP SDK, FAStMCP, SQL, Streamlit

## Workflow:

```text
User asks a natural-language question
  ↓
Host discovers all MCP tools from the MCP server
  ↓
Host-side tool router filters only relevant tools for the question
  ↓
Host sends question + filtered MCP tool schemas to Ollama qwen2.5:7b
  ↓
Ollama LLM decides which exposed tool to call and what arguments to pass
  ↓
Host executes the tool through MCP Client SDK
  ↓
MCP Client talks to MCP Server using MCP JSON-RPC over stdio
  ↓
MCP Server reads/writes SQLite database
  ↓
Tool result goes back to Host
  ↓
Host sends result back to Ollama
  ↓
Ollama explains the result to the end user
```

**Important communication distinction**

```text
Host ↔ Ollama LLM
Uses Ollama Python SDK / local Ollama API

Host ↔ MCP Client SDK
Uses normal Python method calls, such as session.call_tool(...)

MCP Client ↔ MCP Server
Uses MCP protocol, JSON-RPC over stdio

MCP Server ↔ SQLite Database
Uses normal sqlite3 database code
```








## 4. MCP tools exposed by the server

The MCP server exposes many controlled database operations.

### Database discovery tools

```text
list_tables()
describe_table(table_name)
```

### Sales analytics tools

```text
get_sales_summary(region)
get_revenue_by_region()
get_sales_by_channel()
get_daily_sales_trend(start_date, end_date)
get_top_products_by_revenue(limit, region)
```

### Inventory and product tools

```text
check_inventory(product_id)
get_product_details(product_id)
search_products(keyword)
get_low_stock_products(category)
get_supplier_reorder_report(supplier)
```

### Customer and order tools

```text
get_customer_profile(customer_id)
get_customer_orders(customer_id, limit)
get_order_details(order_id)
get_segment_performance()
```

### Verification tools

```text
get_inventory_movements(product_id, limit)
get_audit_log(limit)
```

### Safe read-only SQL tool

```text
run_readonly_sql(query)
```

This tool only allows `SELECT` or `WITH` queries. It blocks risky commands like:

```text
INSERT
UPDATE
DELETE
DROP
ALTER
CREATE
REPLACE
ATTACH
DETACH
VACUUM
```

---

## 5. Write tools

The project intentionally exposes **specific business write tools**, not unrestricted raw SQL.

That is safer and more realistic.

### Create customer

```text
create_customer(customer_name, segment, region, customer_id, signup_date)
```

Example:

```text
Create a new customer named Nova Bakery in the south region with segment B2B.
```

### Update customer segment

```text
update_customer_segment(customer_id, segment)
```

Example:

```text
Update customer C004 segment to Premium Consumer.
```

### Create product

```text
create_product(product_name, category, stock_qty, reorder_level, supplier, unit_cost, unit_price, product_id)
```

Example:

```text
Create a new product called Webcam Stand in Electronics with stock 20, reorder level 5, supplier StandRight, unit cost 18, and price 45.
```

### Update product price

```text
update_product_price(product_id, unit_price)
```

Example:

```text
Update product P200 price to 89.99.
```

### Update reorder level

```text
update_reorder_level(product_id, reorder_level)
```

Example:

```text
Set reorder level for P400 to 15.
```

### Restock inventory

```text
restock_inventory(product_id, quantity, note)
```

Example:

```text
Restock product P200 by 25 units because supplier shipment arrived.
```

### Adjust inventory

```text
adjust_inventory(product_id, change_qty, reason)
```

Examples:

```text
Decrease P300 inventory by 2 because two units were damaged.
```

```text
Increase P400 inventory by 3 after manual warehouse correction.
```

### Create sales order

```text
create_sales_order(customer_id, product_id, units, channel, order_date, region)
```

This tool:

- Validates customer exists
- Validates product exists
- Checks enough inventory is available
- Inserts a row into `sales`
- Calculates revenue using current product price
- Decrements inventory
- Inserts inventory movement history
- Writes an audit log row

Example:

```text
Create a sales order for customer C001 for 2 units of product P200 through online channel, then check P200 inventory.
```

### Cancel order

```text
cancel_order(order_id, reason)
```

This tool:

- Finds the sales order
- Deletes the row from `sales`
- Restocks the inventory quantity from that order
- Inserts inventory movement history
- Writes an audit log row

Example:

```text
Cancel order 3 because the customer requested cancellation.
```

---

## 6. Safety design

This project does **not** expose a dangerous generic write SQL tool like:

```text
run_any_insert_update_delete_sql(...)
```

Instead, the MCP server exposes controlled business actions:

```text
create_sales_order
restock_inventory
adjust_inventory
cancel_order
create_customer
create_product
update_product_price
update_reorder_level
update_customer_segment
```

The Host also asks for human confirmation before executing write tools.

By default:

```env
REQUIRE_WRITE_CONFIRMATION=true
```

When Ollama requests a write tool, the Host shows:

```text
WRITE TOOL REQUESTED
Tool: restock_inventory
Arguments: {...}
Type YES to execute this database write.
```

For easier local demos, you can change `.env` to:

```env
REQUIRE_WRITE_CONFIRMATION=false
```

Recommended understanding:

```text
LLM proposes the database change.
Host approves or blocks it.
MCP server executes approved operations.
Audit log records the write.
```

---


```text
Host tool-router decision
Host -> Ollama request with filtered tools
LLM tool-call decision
Host -> MCP Client call
MCP Client -> MCP Server JSON-RPC boundary
MCP Server database output
Final answer
```

### Out-of-scope questions

If a custom question is not related to this retail database assistant, the Host router can expose zero tools or only a small safe set. The LLM should not call MCP tools for unrelated topics. It should answer with a short message explaining that the demo only supports retail database operations and analytics, such as customers, products, inventory, sales/orders, suppliers, and audit logs.

The UI does not show tool traces, tool arguments, database previews, or internal communication logs. It is only for selecting/entering questions, approving required writes, and viewing the final answer.

To inspect the database, open a separate terminal and run SQLite commands such as:



## 9. What happens during a write example

Command:

```bash
python host_app.py "Restock product P200 by 25 units because supplier shipment arrived, then check P200 inventory."
```

Flow:

```text
1. Host starts MCP server
2. Host discovers all tools registered in MCP server
3. Host runs the tool router and exposes only relevant inventory/write tools
4. Host sends user request + filtered tool schemas to Ollama qwen2.5:7b
5. Ollama decides to call restock_inventory(product_id="P200", quantity=25, note="supplier shipment arrived")
6. Host asks for human confirmation
7. User types YES
8. Host calls MCP Client SDK: session.call_tool(...)
9. MCP Client sends JSON-RPC request to MCP Server over stdio
10. MCP Server updates SQLite inventory table
11. MCP Server inserts inventory_movements row
12. MCP Server inserts db_audit_log row
13. Tool result returns to Host
14. Host sends tool result back to Ollama
15. Ollama may call check_inventory to verify
16. Ollama explains final result to user
```

---

## 10. Troubleshooting

### Problem: `Connection refused` or cannot connect to Ollama

Make sure Ollama is running:

```bash
ollama serve
```

Then try again.

### Problem: model not found

Pull the model:

```bash
ollama pull qwen2.5:7b
```


## 11. Why this project is resume-friendly

This project demonstrates:

- Local/free LLM with Ollama
- qwen2.5:7b tool-calling workflow
- Host-side tool routing to reduce tool schemas sent to the local LLM
- MCP client/server architecture
- MCP JSON-RPC communication over stdio
- SQLite-backed read/write tools
- Transactional order creation
- Inventory updates
- Audit logging
- Human-in-the-loop write approval
- Simple browser UI showing dropdown/custom questions, LLM-triggered write approval, and final answer
- Safe business tools instead of raw write SQL
- Clear separation between Host, LLM, MCP Client, MCP Server, and Database


