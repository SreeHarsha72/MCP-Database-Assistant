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
**Tech stack used:**  Python, Ollama-qwen2.5:7b, MCP SDK, FastMCP, SQL, Streamlit

## Workflow:

```text
User asks a natural-language question
  ↓
Host discovers relevant MCP tools from the MCP server
  ↓
Host sends question +  MCP tool schemas to Ollama qwen2.5:7b
  ↓
Ollama LLM decides which exposed tool to call and what arguments to pass
  ↓
Host executes the respective registered tool through MCP Client SDK
  ↓
MCP Client talks to MCP Server using MCP JSON-RPC over stdio
  ↓
MCP Server reads/writes SQLite database
  ↓
Executed tool result goes back to Host
  ↓
Host sends result back to Ollama
  ↓
Ollama explains the result to the end user
```

**Important communication distinction**

```text
Host ↔ Ollama LLM
Uses Ollama Python SDK / Ollama API

Host ↔ MCP Client SDK
Uses normal Python method calls, such as session.call_tool(...)

MCP Client ↔ MCP Server
Uses MCP protocol, JSON-RPC over stdio

MCP Server ↔ SQLite Database
Uses normal python, sqlite3 database code
```


## Tools registered in the MCP server

The MCP server has below registered tools which are the controlled database operations.

**For reading operations:**
- list_tables
- describe_table
- get_sales_summary
- get_revenue_by_region
- get_sales_by_channel
- get_daily_sales_trend
- check_inventory
- get_product_details
- search_products
- get_low_stock_products
- get_supplier_reorder_report
- get_top_products_by_revenue
- get_customer_profile
- get_customer_orders
- get_order_details
- get_segment_performance
- run_readonly_sql

**For writing operations:** The Host asks for human confirmation before executing write tools.
- create_customer
- update_customer_segment
- create_product
- update_product_price
- update_reorder_level
- restock_inventory
- adjust_inventory
- create_sales_order
- cancel_order

**For tracking/auditing operations:**
- get_inventory_movements
- get_audit_log



## Handling Out-of-scope questions
Out-of-context handling is mainly done through the Host’s system prompt. If a custom question is not related to this retail database assistant, the LLM should not call MCP tools for unrelated topics. It should answer with a short message explaining that the demo only supports retail database operations and analytics, such as customers, products, inventory, sales/orders, suppliers, and audit logs.


