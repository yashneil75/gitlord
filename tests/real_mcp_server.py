from mcp.server.fastmcp import FastMCP

mcp = FastMCP("test-server")

@mcp.tool()
def calculator(operation: str, a: float, b: float) -> str:
    """Simple calculator with add, subtract, multiply, divide"""
    if operation == "add":
        return str(a + b)
    elif operation == "subtract":
        return str(a - b)
    elif operation == "multiply":
        return str(a * b)
    elif operation == "divide":
        if b == 0:
            return "Error: division by zero"
        return str(a / b)
    return f"Unknown operation: {operation}"

@mcp.tool()
def echo(msg: str) -> str:
    return f"You said: {msg}"

mcp.run(transport="stdio")
