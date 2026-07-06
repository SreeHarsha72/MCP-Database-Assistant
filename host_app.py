"""
This app connects three layers:

1. Host <-> local Ollama LLM using the Ollama Python SDK
2. Host <-> MCP Client SDK using normal Python method calls
3. MCP Client SDK <-> MCP Server using MCP JSON-RPC over stdio
4. MCP Server <-> real SQLite database using sqlite3

This version uses a free/local LLM:
    qwen2.5:7b through Ollama

"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from ollama import Client

load_dotenv()

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
SERVER_PATH = Path(__file__).with_name("mcp_server.py")
MAX_TOOL_ROUNDS = int(os.getenv("MAX_TOOL_ROUNDS", "3"))
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
REQUIRE_WRITE_CONFIRMATION = os.getenv("REQUIRE_WRITE_CONFIRMATION", "true").strip().lower() != "false"

WRITE_TOOL_NAMES = {
    "create_customer",
    "update_customer_segment",
    "create_product",
    "update_product_price",
    "update_reorder_level",
    "restock_inventory",
    "adjust_inventory",
    "create_sales_order",
    "cancel_order",
}


# The MCP server can register many tools. The Host does not need to send every tool
# to the LLM for every question. This router narrows the tool list first.
# That improves speed, reduces confusion, and prevents unrelated write tools from
# being visible to the model when they are not needed.
TOOL_GROUPS: dict[str, set[str]] = {
    "discovery": {
        "list_tables",
        "describe_table",
        "run_readonly_sql",
    },
    "sales_analytics": {
        "get_sales_summary",
        "get_revenue_by_region",
        "get_sales_by_channel",
        "get_daily_sales_trend",
        "get_top_products_by_revenue",
        "get_order_details",
        "get_customer_orders",
        "run_readonly_sql",
    },
    "inventory": {
        "check_inventory",
        "get_product_details",
        "search_products",
        "get_low_stock_products",
        "get_supplier_reorder_report",
        "get_inventory_movements",
        "get_audit_log",
        "run_readonly_sql",
    },
    "inventory_write": {
        "restock_inventory",
        "adjust_inventory",
        "check_inventory",
        "get_product_details",
        "get_inventory_movements",
        "get_audit_log",
    },
    "orders_write": {
        "create_sales_order",
        "cancel_order",
        "check_inventory",
        "get_order_details",
        "get_customer_orders",
        "get_inventory_movements",
        "get_audit_log",
    },
    "customers": {
        "get_customer_profile",
        "get_customer_orders",
        "get_segment_performance",
        "create_customer",
        "update_customer_segment",
        "get_audit_log",
        "run_readonly_sql",
    },
    "products": {
        "get_product_details",
        "search_products",
        "get_top_products_by_revenue",
        "create_product",
        "update_product_price",
        "update_reorder_level",
        "check_inventory",
        "get_audit_log",
        "run_readonly_sql",
    },
    "audit": {
        "get_audit_log",
        "get_inventory_movements",
    },
}

ROUTER_KEYWORDS: dict[str, list[str]] = {
    "discovery": [
        "table", "tables", "schema", "column", "columns", "database", "db", "sql",
        "what data", "what fields", "show structure", "describe",
    ],
    "sales_analytics": [
        "sales", "sale", "revenue", "profit", "summary", "channel", "trend", "daily",
        "top product", "top products", "performance", "region", "west", "east", "north", "south",
    ],
    "inventory": [
        "inventory", "stock", "quantity", "qty", "available", "low stock", "reorder", "supplier",
        "shipment", "warehouse", "movement", "movements",
    ],
    "inventory_write": [
        "restock", "restocked", "restocking", "adjust inventory", "increase inventory",
        "decrease inventory", "damaged", "manual correction", "supplier shipment", "shipment arrived",
    ],
    "orders_write": [
        "create order", "create a sales order", "new order", "place order", "purchase order",
        "customer ordered", "buy", "bought", "cancel order", "cancel sales order", "cancelled order",
    ],
    "customers": [
        "customer", "customers", "segment", "profile", "signup", "nova bakery", "b2b", "consumer",
    ],
    "products": [
        "product", "products", "price", "category", "unit price", "unit cost", "reorder level",
        "create product", "new product", "update price",
    ],
    "audit": [
        "audit", "audit log", "logs", "history", "recent changes", "change history",
    ],
}

OUT_OF_SCOPE_EXAMPLES = [
    "weather", "capital of", "president", "recipe", "movie", "sports", "stock market",
    "leetcode", "python code", "java code", "essay", "resume", "interview question", "triangle", "theorem"
]


def looks_out_of_scope(user_question: str) -> bool:
    """Very small deterministic guard for obvious non-retail-demo questions."""
    q = user_question.lower()
    if any(text in q for text in OUT_OF_SCOPE_EXAMPLES):
        retail_words = {
            "retail", "customer", "product", "inventory", "sales", "order", "supplier",
            "audit", "database", "table", "stock", "revenue",
        }
        return not any(word in q for word in retail_words)
    return False


def select_relevant_mcp_tools(user_question: str, mcp_tools: list[Any]) -> tuple[list[Any], list[str]]:
    """
    Host-side tool router.

    The MCP server still registers all tools. The Host discovers all of them,
    then sends only relevant tool schemas to the LLM for this specific question.

    Returns:
      - selected MCP tool objects
      - selected category names for terminal/debug logs
    """
    available_by_name = {tool.name: tool for tool in mcp_tools}
    q = user_question.lower()

    if looks_out_of_scope(user_question):
        return [], ["out_of_scope"]

    selected_categories: list[str] = []
    selected_names: set[str] = set()

    for category, keywords in ROUTER_KEYWORDS.items():
        if any(keyword in q for keyword in keywords):
            selected_categories.append(category)
            selected_names.update(TOOL_GROUPS[category])

    # Important cross-category rules.
    # Order creation/cancellation also changes inventory, so expose inventory verification tools.
    if "orders_write" in selected_categories:
        selected_names.update(TOOL_GROUPS["inventory"])

    # Product price/update questions often need product details after the update.
    if "products" in selected_categories:
        selected_names.update({"get_product_details", "search_products"})

    # Customer questions often need order history/performance context.
    if "customers" in selected_categories:
        selected_names.update({"get_customer_orders", "get_segment_performance"})

    # For vague but still retail/database-flavored questions, send a small safe read-only set.
    retail_domain_words = [
        "retail", "store", "stores", "dashboard", "analytics", "data", "records",
    ]
    if not selected_names and any(word in q for word in retail_domain_words):
        selected_categories.append("safe_fallback")
        selected_names.update({
            "list_tables",
            "describe_table",
            "get_sales_summary",
            "check_inventory",
            "search_products",
            "get_audit_log",
        })

    selected_tools = [available_by_name[name] for name in selected_names if name in available_by_name]

    # Stable order matching the server's discovered order helps terminal logs and reproducibility.
    selected_tools.sort(key=lambda tool: list(available_by_name).index(tool.name))
    return selected_tools, selected_categories


def boundary(step: str, message: str, trace: list[dict[str, Any]] | None = None, verbose: bool = True) -> None:
    """Record and optionally print a communication boundary."""
    if trace is not None:
        trace.append({"step": step, "message": message})
    if verbose:
        print(f"\n[{step}] {message}")


def mcp_tool_to_ollama_tool(tool: Any) -> dict[str, Any]:
    """
    Convert an MCP tool schema into an Ollama-compatible function tool schema.

    MCP exposes:
      - tool.name
      - tool.description
      - tool.inputSchema / tool.input_schema

    Ollama accepts OpenAI-style tool definitions:
      {
        "type": "function",
        "function": {
          "name": "tool_name",
          "description": "...",
          "parameters": {... JSON schema ...}
        }
      }
    """
    input_schema = (
        getattr(tool, "inputSchema", None)
        or getattr(tool, "input_schema", None)
        or {"type": "object", "properties": {}}
    )

    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": input_schema,
        },
    }


def mcp_result_to_text(result: Any) -> str:
    """
    Convert MCP CallToolResult content into plain text for the LLM.
    Most FastMCP string returns arrive as TextContent objects.
    """
    text_parts: list[str] = []

    for item in getattr(result, "content", []) or []:
        if hasattr(item, "text"):
            text_parts.append(item.text)
        elif hasattr(item, "model_dump_json"):
            text_parts.append(item.model_dump_json())
        else:
            text_parts.append(str(item))

    structured = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
    if structured:
        text_parts.append(json.dumps(structured, indent=2, default=str))

    return "\n".join(text_parts).strip() or str(result)


def obj_get(obj: Any, key: str, default: Any = None) -> Any:
    """Read from dict-like or object-like SDK response values."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def message_to_dict(message: Any) -> dict[str, Any]:
    """Convert an Ollama SDK message object/dict into a plain dict for message history."""
    if isinstance(message, dict):
        msg = dict(message)
    elif hasattr(message, "model_dump"):
        msg = message.model_dump(exclude_none=True)
    else:
        msg = {
            "role": obj_get(message, "role", "assistant"),
            "content": obj_get(message, "content", ""),
        }
        tool_calls = obj_get(message, "tool_calls", None)
        if tool_calls:
            msg["tool_calls"] = [tool_call_to_dict(tc) for tc in tool_calls]

    msg.setdefault("role", "assistant")
    msg.setdefault("content", "")
    return msg


def tool_call_to_dict(tool_call: Any) -> dict[str, Any]:
    """Convert an Ollama tool call object/dict into a plain dict."""
    if isinstance(tool_call, dict):
        return tool_call
    if hasattr(tool_call, "model_dump"):
        return tool_call.model_dump(exclude_none=True)

    function = obj_get(tool_call, "function", {})
    if isinstance(function, dict):
        function_dict = function
    elif hasattr(function, "model_dump"):
        function_dict = function.model_dump(exclude_none=True)
    else:
        function_dict = {
            "name": obj_get(function, "name", ""),
            "arguments": obj_get(function, "arguments", {}),
        }

    return {"function": function_dict}


def extract_tool_calls(message: Any) -> list[dict[str, Any]]:
    """Return normalized tool calls from an Ollama assistant message."""
    tool_calls = obj_get(message, "tool_calls", None) or []
    return [tool_call_to_dict(tc) for tc in tool_calls]


def parse_tool_call(tool_call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Extract tool name and arguments from an Ollama/OpenAI-style tool call."""
    function = tool_call.get("function", {}) or {}
    tool_name = function.get("name", "")
    raw_arguments = function.get("arguments", {}) or {}

    if isinstance(raw_arguments, str):
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            arguments = {}
    elif isinstance(raw_arguments, dict):
        arguments = raw_arguments
    else:
        arguments = {}

    return tool_name, arguments


def confirm_write_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    auto_confirm_writes: bool = False,
    verbose: bool = True,
    interactive_confirmation: bool = True,
) -> bool:
    """
    Human-in-the-loop guard for write tools.

    The LLM can request a write, but the Host controls whether the DB write actually executes.
    Streamlit can pass auto_confirm_writes=True when the user explicitly enables writes for a request.
    """
    if tool_name not in WRITE_TOOL_NAMES or not REQUIRE_WRITE_CONFIRMATION:
        return True

    if auto_confirm_writes:
        if verbose:
            print(f"\nWRITE TOOL AUTO-CONFIRMED BY HOST UI: {tool_name}")
            print(json.dumps(arguments, indent=2, default=str))
        return True

    # Non-interactive callers such as Streamlit can keep verbose terminal logs
    # while preventing the app from waiting for terminal input.
    if not interactive_confirmation:
        if verbose:
            print("Host cancelled write tool execution because UI approval was not provided.")
        return False

    # Non-interactive callers can also set verbose=False to cancel safely.
    if not verbose:
        return False

    print("\nWRITE TOOL REQUESTED")
    print(f"Tool: {tool_name}")
    print("Arguments:")
    print(json.dumps(arguments, indent=2, default=str))
    print("\nType YES to execute this database write. Anything else will cancel it.")
    try:
        answer = input("Confirm write? ").strip()
    except EOFError:
        answer = ""
    return answer == "YES"


def build_system_prompt() -> str:
    return (
        "You are a retail operations and analytics assistant connected to an MCP database server. "
        "Your scope is only this retail database demo: customers, products, inventory, sales/orders, suppliers, "
        "inventory movements, and audit logs. "
        "The Host may expose only a relevant subset of MCP tools for this specific request. "
        "Use the exposed tools whenever the user asks about database data or wants to create, update, restock, adjust, cancel, or verify data. "
        "For read questions, prefer specific read tools before run_readonly_sql. "
        "For write/change questions, use specific write tools such as create_sales_order, restock_inventory, adjust_inventory, "
        "create_customer, create_product, update_product_price, update_reorder_level, update_customer_segment, or cancel_order. "
        "Only use write tools when the user clearly asks to modify database data. "
        "Do not invent database numbers. Base database answers only on tool results. "
        "After write operations, verify the result using read tools when helpful, such as check_inventory, get_order_details, "
        "get_audit_log, or get_inventory_movements. "
        "If the user asks something outside this retail database assistant scope, do not call tools. "
        "Politely say that this demo can help only with retail database operations and analytics, and ask them to choose or ask "
        "a question about customers, products, inventory, sales/orders, suppliers, or audit logs. "
        "Explain the final answer in simple English."
    )



async def run_query(
    user_query: str,
    *,
    auto_confirm_writes: bool = False,
    verbose: bool = True,
    interactive_write_confirmation: bool = True,
    stop_before_writes: bool = False,
) -> dict[str, Any]:
    trace: list[dict[str, Any]] = []
    executed_tool_calls: list[dict[str, Any]] = []
    discovered_tools: list[str] = []

    boundary("0", f"Host will use local Ollama model: {OLLAMA_MODEL} at {OLLAMA_HOST}", trace, verbose)
    boundary("0A", f"Host config: MAX_TOOL_ROUNDS={MAX_TOOL_ROUNDS}, OLLAMA_KEEP_ALIVE={OLLAMA_KEEP_ALIVE}", trace, verbose)
    boundary("1", "Host starts MCP server as a subprocess using stdio transport", trace, verbose)

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_PATH)],
        env=os.environ.copy(),
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            boundary("2", "Host initializes MCP client session", trace, verbose)
            await session.initialize()

            boundary("3", "Host asks MCP Server for available tools: session.list_tools()", trace, verbose)
            tools_result = await session.list_tools()
            mcp_tools = tools_result.tools
            discovered_tools = [tool.name for tool in mcp_tools]
            if verbose:
                print("MCP tools discovered:", discovered_tools)

            selected_mcp_tools, selected_categories = select_relevant_mcp_tools(user_query, mcp_tools)
            exposed_tool_names = [tool.name for tool in selected_mcp_tools]
            allowed_tool_names = set(exposed_tool_names)
            ollama_tools = [mcp_tool_to_ollama_tool(tool) for tool in selected_mcp_tools]

            boundary(
                "3A",
                "Host tool router selected relevant MCP tools before sending schemas to the LLM",
                trace,
                verbose,
            )
            if verbose:
                print("Router categories:", selected_categories)
                print(f"Tools exposed to LLM ({len(exposed_tool_names)} of {len(discovered_tools)}):", exposed_tool_names)

            ollama_client = Client(host=OLLAMA_HOST)

            messages: list[dict[str, Any]] = [
                {"role": "system", "content": build_system_prompt()},
                {"role": "user", "content": user_query},
            ]

            for round_number in range(1, MAX_TOOL_ROUNDS + 1):
                boundary(
                    str(3 + round_number),
                    "Host -> local LLM through Ollama SDK with messages + filtered MCP tool schemas",
                    trace,
                    verbose,
                )

                chat_kwargs: dict[str, Any] = {
                    "model": OLLAMA_MODEL,
                    "messages": messages,
                    "options": {"temperature": 0},
                    "keep_alive": OLLAMA_KEEP_ALIVE,
                }
                if ollama_tools:
                    chat_kwargs["tools"] = ollama_tools

                response = ollama_client.chat(**chat_kwargs)

                assistant_msg = obj_get(response, "message", {})
                normalized_assistant_msg = message_to_dict(assistant_msg)
                messages.append(normalized_assistant_msg)

                tool_calls = extract_tool_calls(assistant_msg)

                if not tool_calls:
                    boundary("FINAL", "Ollama LLM -> Host -> End user final answer", trace, verbose)
                    final_answer = normalized_assistant_msg.get("content", "")
                    if verbose:
                        print("\nFinal answer:\n")
                        print(final_answer)
                    return {
                        "final_answer": final_answer,
                        "trace": trace,
                        "tool_calls": executed_tool_calls,
                        "discovered_tools": discovered_tools,
                        "exposed_tools": exposed_tool_names,
                        "router_categories": selected_categories,
                        "requires_write_approval": False,
                        "pending_write_tools": [],
                    }

                # Streamlit uses this preflight mode so the LLM can decide whether a write tool is needed.
                # If a write is needed, the Host returns before executing it so the UI can ask for human approval.
                if stop_before_writes:
                    pending_write_tools: list[dict[str, Any]] = []
                    for pending_tool_call in tool_calls:
                        pending_tool_name, pending_arguments = parse_tool_call(pending_tool_call)
                        if pending_tool_name in WRITE_TOOL_NAMES:
                            pending_write_tools.append(
                                {
                                    "tool_name": pending_tool_name,
                                    "arguments": pending_arguments,
                                    "is_write_tool": True,
                                    "executed": False,
                                    "output": "Waiting for UI write approval before execution.",
                                }
                            )

                    if pending_write_tools:
                        boundary(
                            "WRITE_APPROVAL_REQUIRED",
                            "LLM selected a database write tool. Host paused before execution for human approval.",
                            trace,
                            verbose,
                        )
                        if verbose:
                            print("Pending write tools:")
                            print(json.dumps(pending_write_tools, indent=2, default=str))
                        return {
                            "final_answer": "",
                            "requires_write_approval": True,
                            "pending_write_tools": pending_write_tools,
                            "trace": trace,
                            "tool_calls": executed_tool_calls + pending_write_tools,
                            "discovered_tools": discovered_tools,
                            "exposed_tools": exposed_tool_names,
                            "router_categories": selected_categories,
                        }

                boundary("TOOL", "Ollama LLM -> Host: model requested one or more tool calls", trace, verbose)

                for tool_call in tool_calls:
                    tool_name, arguments = parse_tool_call(tool_call)
                    if verbose:
                        print(f"LLM requested tool: {tool_name}")
                        print(f"Tool arguments: {arguments}")

                    call_record = {
                        "tool_name": tool_name,
                        "arguments": arguments,
                        "is_write_tool": tool_name in WRITE_TOOL_NAMES,
                        "executed": False,
                        "output": "",
                    }

                    if not tool_name:
                        tool_output_text = json.dumps(
                            {"success": False, "message": "Tool call did not include a tool name."},
                            indent=2,
                        )
                    elif tool_name not in allowed_tool_names:
                        tool_output_text = json.dumps(
                            {
                                "success": False,
                                "blocked_by_host_router": True,
                                "message": f"Tool '{tool_name}' was not exposed for this question by the Host tool router.",
                            },
                            indent=2,
                        )
                        if verbose:
                            print(f"Host blocked non-exposed tool call: {tool_name}")
                    elif not confirm_write_tool(
                        tool_name,
                        arguments,
                        auto_confirm_writes=auto_confirm_writes,
                        verbose=verbose,
                        interactive_confirmation=interactive_write_confirmation,
                    ):
                        tool_output_text = json.dumps(
                            {
                                "success": False,
                                "cancelled_by_host": True,
                                "message": "The Host cancelled this database write because human confirmation was not provided.",
                            },
                            indent=2,
                        )
                        if verbose:
                            print("Host cancelled write tool execution.")
                        call_record["executed"] = False
                    else:
                        boundary("MCP", "Host -> MCP Client SDK: session.call_tool(...) normal Python method call", trace, verbose)
                        mcp_result = await session.call_tool(tool_name, arguments=arguments)

                        boundary("JSON-RPC", "MCP Client SDK <-> MCP Server: MCP JSON-RPC over stdio happens inside the SDK", trace, verbose)
                        tool_output_text = mcp_result_to_text(mcp_result)
                        call_record["executed"] = True
                        if verbose:
                            print("Tool output returned to Host:")
                            print(tool_output_text)

                    call_record["output"] = tool_output_text
                    executed_tool_calls.append(call_record)

                    # Ollama expects tool results as role='tool'. The name helps the model connect result to the requested tool.
                    messages.append(
                        {
                            "role": "tool",
                            "name": tool_name,
                            "content": tool_output_text,
                        }
                    )

            boundary("STOP", f"Reached MAX_TOOL_ROUNDS={MAX_TOOL_ROUNDS}. Returning partial result.", trace, verbose)
            final_response = ollama_client.chat(
                model=OLLAMA_MODEL,
                messages=messages
                + [
                    {
                        "role": "user",
                        "content": "Summarize what was completed and what still needs another step.",
                    }
                ],
                options={"temperature": 0},
                keep_alive=OLLAMA_KEEP_ALIVE,
            )
            final_msg = message_to_dict(obj_get(final_response, "message", {}))
            final_answer = final_msg.get("content", "")
            if verbose:
                print("\nFinal answer:\n")
                print(final_answer)
            return {
                "final_answer": final_answer,
                "trace": trace,
                "tool_calls": executed_tool_calls,
                "discovered_tools": discovered_tools,
                "exposed_tools": exposed_tool_names,
                "router_categories": selected_categories,
                "requires_write_approval": False,
                "pending_write_tools": [],
            }


def main() -> None:
    parser = argparse.ArgumentParser(description="Ollama + MCP + SQLite Host CLI")
    parser.add_argument("query", nargs="*", help="Natural language request for the assistant")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Auto-confirm write tools for this request. Use only for demos/tests.",
    )
    args = parser.parse_args()

    user_query = " ".join(args.query).strip()
    if not user_query:
        user_query = "Create a sales order for customer C001 for 2 units of product P200, then check product P200 inventory."

    asyncio.run(run_query(user_query, auto_confirm_writes=args.yes, verbose=True))


if __name__ == "__main__":
    main()
