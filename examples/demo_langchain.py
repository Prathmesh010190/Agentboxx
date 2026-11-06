"""LangChain integration demo with Agentboxx flight recorder.

Runs offline without an API key using LangChain tools and AgentboxxCallbackHandler.
"""
from langchain_core.tools import tool

from agentbox.integrations.langchain import AgentboxxCallbackHandler


@tool
def calculate_area(length: float, width: float) -> float:
    """Calculate the area of a rectangle."""
    return length * width


@tool
def reverse_text(text: str) -> str:
    """Reverse a string."""
    return text[::-1]


def main():
    print("=== Agentboxx LangChain Integration Demo ===\n")

    # Initialize handler with session ID and runs directory
    handler = AgentboxxCallbackHandler(
        session_id="langchain-demo",
        log_dir="./runs",
        overwrite=True,
    )

    print("1. Executing tool 'calculate_area'...")
    res1 = calculate_area.invoke({"length": 12.5, "width": 4.0}, config={"callbacks": [handler]})
    print(f"   -> Result: {res1}\n")

    print("2. Executing tool 'reverse_text'...")
    res2 = reverse_text.invoke({"text": "Agentboxx tamper-evident flight recorder"}, config={"callbacks": [handler]})
    print(f"   -> Result: {res2}\n")

    # Finalize log
    handler.close("demo completed")

    print(f"Flight log written to: {handler.log_path}")
    print("Verify the log with:")
    print(f"    agentbox verify {handler.log_path}")
    print("View the human-readable report with:")
    print(f"    agentbox report {handler.log_path}")


if __name__ == "__main__":
    main()

