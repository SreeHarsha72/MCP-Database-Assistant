"""
Simple Streamlit UI - Ollama + MCP + SQLite
Run:
    streamlit run streamlit_app.py
"""

from __future__ import annotations

import asyncio
from typing import Any

import streamlit as st
from dotenv import load_dotenv

from host_app import OLLAMA_HOST, OLLAMA_MODEL, run_query

load_dotenv()

st.set_page_config(
    page_title="MCP DB Assistant",
    layout="centered",
)

SAMPLE_QUESTIONS: list[str] = [
    "What is the west region sales summary?",
    "Which products are low in stock and which supplier should I contact?",
    "Show top 3 products by revenue in the west region.",
    "Give me customer profile for C001 and show their recent orders.",
    "Which customer segment has the highest revenue?",
    "Show the latest 10 database audit log entries.",
    "Restock product P200 by 25 units because supplier shipment arrived, then check P200 inventory.",
    "Create a sales order for customer C001 for 2 units of product P200 through online channel, then check P200 inventory.",
    "Update product P200 price to 89.99, then show product details for P200.",
    "Create a new customer named Nova Bakery in the south region with segment B2B, then show the recent audit log.",
    "Cancel order 3 because the customer requested cancellation, then check the product inventory.",
]


def run_async_query(
    question: str,
    *,
    allow_writes: bool,
    stop_before_writes: bool,
) -> dict[str, Any]:
    """Run async Host logic from Streamlit.

    verbose=True keeps all processing visible in the terminal.
    interactive_write_confirmation=False prevents the browser UI from waiting for terminal input.
    stop_before_writes=True lets the LLM choose tools first, then pauses before any DB write.
    """
    async def _runner() -> dict[str, Any]:
        return await run_query(
            question,
            auto_confirm_writes=allow_writes,
            verbose=True,
            interactive_write_confirmation=False,
            stop_before_writes=stop_before_writes,
        )

    try:
        return asyncio.run(_runner())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(_runner())
        finally:
            loop.close()


if "final_answer" not in st.session_state:
    st.session_state.final_answer = ""
if "needs_write_approval" not in st.session_state:
    st.session_state.needs_write_approval = False
if "pending_question" not in st.session_state:
    st.session_state.pending_question = ""
if "last_error" not in st.session_state:
    st.session_state.last_error = ""

st.title("MCP Database Assistant")
st.caption(f"Local LLM: {OLLAMA_MODEL} | Ollama: {OLLAMA_HOST}")

question_options = ["Select a question..."] + SAMPLE_QUESTIONS + ["Other"]
selected_option = st.selectbox("Questions", question_options)

custom_question = ""
if selected_option == "Other":
    custom_question = st.text_input("Enter your question")

question = custom_question.strip() if selected_option == "Other" else selected_option

run_clicked = st.button("Run", type="primary")

if run_clicked:
    st.session_state.final_answer = ""
    st.session_state.needs_write_approval = False
    st.session_state.pending_question = ""
    st.session_state.last_error = ""

    if selected_option == "Select a question...":
        st.warning("Please select a question.")
    elif selected_option == "Other" and not question:
        st.warning("Please enter a question.")
    else:
        print("\n" + "=" * 90)
        print("STREAMLIT UI REQUEST - PREFLIGHT")
        print(f"Question: {question}")
        print("The LLM will decide whether this needs a write tool.")
        print("=" * 90)

        try:
            result = run_async_query(
                question,
                allow_writes=False,
                stop_before_writes=True,
            )
            if result.get("requires_write_approval"):
                st.session_state.needs_write_approval = True
                st.session_state.pending_question = question
                print("UI paused because LLM selected a DB write tool. Waiting for browser approval.")
            else:
                st.session_state.final_answer = result.get("final_answer") or "No final answer returned."
        except Exception as exc:
            print("STREAMLIT UI ERROR")
            print(repr(exc))
            st.session_state.last_error = f"Request failed: {exc}"

if st.session_state.needs_write_approval:
    approve_write = st.checkbox("Approve database write operation")
    approve_clicked = st.button("Run approved request", type="primary")

    if approve_clicked:
        if not approve_write:
            st.warning("Approve the database write before running this request.")
        else:
            print("\n" + "=" * 90)
            print("STREAMLIT UI REQUEST - APPROVED WRITE EXECUTION")
            print(f"Question: {st.session_state.pending_question}")
            print("The Host will allow write tools requested by the LLM.")
            print("=" * 90)

            try:
                result = run_async_query(
                    st.session_state.pending_question,
                    allow_writes=True,
                    stop_before_writes=False,
                )
                st.session_state.final_answer = result.get("final_answer") or "No final answer returned."
                st.session_state.needs_write_approval = False
                st.session_state.pending_question = ""
            except Exception as exc:
                print("STREAMLIT UI ERROR")
                print(repr(exc))
                st.session_state.last_error = f"Request failed: {exc}"

if st.session_state.final_answer:
    st.markdown("### Answer")
    st.write(st.session_state.final_answer)

if st.session_state.last_error:
    st.error(st.session_state.last_error)
