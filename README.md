# MCP-Database-Assistant

This project demonstrates a real MCP communication flow between Ollama + MCP + SQLite:

Proejct Structure:
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


The main goal is to show this flow clearly:

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

---

## 1. Important communication distinction

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

The LLM does **not** directly touch the database.

```text
Host filters what tools are visible.
LLM decides from the exposed tools.
Host controls and executes.
MCP server performs the database operation.
Database stores the truth.
```

---

## Host-side tool router

The MCP server still registers all tools, but the Host no longer sends all 28 tool schemas to the LLM for every question.

Instead:

```text
Question: "Restock product P200 by 25 units, then check inventory"
  ↓
Host detects inventory/write intent
  ↓
Host exposes only relevant tools such as:
restock_inventory, check_inventory, get_inventory_movements, get_audit_log
  ↓
LLM chooses the exact tool and arguments
```

This improves:

- Response time, because the local LLM reads fewer tool schemas
- Accuracy, because the model chooses from fewer tools
- Safety, because unrelated write tools are not exposed when not needed

The terminal prints the router decision for every request:

```text
Router categories: ['inventory', 'inventory_write', 'products']
Tools exposed to LLM (8 of 28): [...]
```

The Host router is rule-based. The LLM is still responsible for deciding the final tool call and arguments from the filtered tool list.

---

## 2. Project files

```text

```

---

## 3. Database tables

The demo database contains:

```text
customers
inventory
sales
inventory_movements
db_audit_log
```

### `customers`

```text
customer_id
customer_name
segment
region
signup_date
```

### `inventory`

```text
product_id
product_name
category
stock_qty
reorder_level
supplier
unit_cost
unit_price
```

### `sales`

```text
order_id
order_date
customer_id
region
product_id
units
revenue
channel
```

### `inventory_movements`

```text
movement_id
product_id
change_qty
reason
reference_id
created_at
```

### `db_audit_log`

```text
audit_id
action
table_name
record_key
details_json
created_at
```

---

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

## 7. Setup and run steps

### Step 1: Install Ollama

Download and install Ollama from the official Ollama website.

After installing, open a terminal and check:

```bash
ollama --version
```

### Step 2: Pull the free local model

```bash
ollama pull qwen2.5:7b
```

You can test the model:

```bash
ollama run qwen2.5:7b
```

Then type:

```text
Hello
```

Exit the Ollama chat with:

```text
/bye
```

### Step 3: Make sure Ollama server is running

Usually Ollama runs automatically after installation.

If needed, start it manually:

```bash
ollama serve
```

Keep that terminal open.

### Step 4: Open the project folder

```bash
cd real_llm_mcp_db_project
```

### Step 5: Create Python virtual environment

```bash
python -m venv .venv
```

Activate on Windows:

```bash
.venv\Scripts\activate
```

Activate on macOS/Linux:

```bash
source .venv/bin/activate
```

### Step 6: Install dependencies

```bash
pip install -r requirements.txt
```

### Step 7: Create `.env`

On Windows:

```bash
copy .env.example .env
```

On macOS/Linux:

```bash
cp .env.example .env
```

Your `.env` should look like this:

```env
OLLAMA_MODEL=qwen2.5:7b
OLLAMA_HOST=http://localhost:11434
DATABASE_PATH=retail.db
REQUIRE_WRITE_CONFIRMATION=true
MAX_TOOL_ROUNDS=3
OLLAMA_KEEP_ALIVE=30m
```

### Step 8: Reset/create database

```bash
python init_db.py
```

### Step 9A: Run the browser UI

Start the Streamlit app:

```bash
streamlit run streamlit_app.py
```

Then open the local URL Streamlit shows, usually:

```text
http://localhost:8501
```

The browser UI is intentionally simple. It shows only:

```text
Questions dropdown
Other option for a custom question
Run button
Final answer
Conditional write approval checkbox
```

The write approval checkbox does not appear based on a hardcoded question list. First, the Host filters the MCP tool list, then sends the question and only the relevant tool schemas to Ollama. If the LLM selects a write tool, the Host pauses before execution and the UI then shows the approval checkbox.

All detailed processing is printed in the terminal where Streamlit is running:

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

```bash
sqlite3 retail.db
```

Then run queries such as:

```sql
.headers on
.mode column
SELECT * FROM sales ORDER BY order_id DESC LIMIT 10;
SELECT * FROM inventory WHERE product_id = 'P200';
SELECT * FROM inventory_movements ORDER BY movement_id DESC LIMIT 10;
SELECT * FROM db_audit_log ORDER BY audit_id DESC LIMIT 10;
```

### Step 9B: Run the terminal CLI

Read example:

```bash
python host_app.py "What is the west region sales summary?"
```

Write example:

```bash
python host_app.py "Restock product P200 by 25 units because supplier shipment arrived, then check P200 inventory."
```

Since this is a write operation, the Host will ask:

```text
Confirm write?
```

Type exactly:

```text
YES
```

For demo/testing only, you can auto-confirm a CLI write request with:

```bash
python host_app.py --yes "Restock product P200 by 25 units because supplier shipment arrived, then check P200 inventory."
```

---

## 8. More example questions

### Check inventory

```bash
python host_app.py "Check inventory for product P200."
```

### Create sales order

```bash
python host_app.py "Create a sales order for customer C001 for 2 units of product P200 through online channel, then check P200 inventory."
```

### Restock product

```bash
python host_app.py "Restock product P200 by 25 units because supplier shipment arrived, then show inventory movement history for P200."
```

### Manual stock adjustment

```bash
python host_app.py "Decrease P300 inventory by 2 because two units were damaged, then show recent audit logs."
```

### Create customer

```bash
python host_app.py "Create a new customer named Nova Bakery in the south region with segment B2B, then show the recent audit log."
```

### Create product

```bash
python host_app.py "Create a new product called Webcam Stand in Electronics with stock 20, reorder level 5, supplier StandRight, unit cost 18, and price 45."
```

### Update price

```bash
python host_app.py "Update product P200 price to 89.99, then show product details for P200."
```

### Cancel order

```bash
python host_app.py "Cancel order 3 because the customer requested cancellation, then check the product inventory."
```

### Show audit logs

```bash
python host_app.py "Show the latest 10 database audit log entries."
```

---

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

### Problem: model gives final text without calling tools

Try asking the question more directly:

```bash
python host_app.py "Use the database tools to check inventory for product P200."
```

Local models are less reliable than paid hosted frontier models for tool calling. `qwen2.5:7b` is a good free starting point, but you can also test:

```bash
ollama pull llama3.1:8b
ollama pull llama3-groq-tool-use:8b
```

Then change `.env`:

```env
OLLAMA_MODEL=llama3.1:8b
```

or:

```env
OLLAMA_MODEL=llama3-groq-tool-use:8b
```

### Problem: write operation did not happen

In the terminal CLI, the Host asks for confirmation.

You must type exactly:

```text
YES
```

In the Streamlit UI, click **Run** first. If the LLM selects a write tool, the UI will then show:

```text
Approve database write operation
```

Check it and click **Run approved request**.

Or set this in `.env` for demos:

```env
REQUIRE_WRITE_CONFIRMATION=false
```

### Problem: Streamlit command not found

Make sure dependencies are installed inside your virtual environment:

```bash
pip install -r requirements.txt
```

Then run:

```bash
streamlit run streamlit_app.py
```

### Problem: database state is messy after testing

Reset the database:

```bash
python init_db.py
```

---

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

Resume bullet:

```text
Built a local LLM-powered MCP database assistant using Ollama qwen2.5:7b, Host-side tool routing, MCP client/server communication over stdio, SQLite-backed read/write tools, transactional inventory/order updates, audit logging, human-in-the-loop approval for database-changing operations, and a simple Streamlit UI for question selection and final answers.
```
