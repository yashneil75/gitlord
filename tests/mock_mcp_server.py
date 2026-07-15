import json
import sys

TOOLS = [
    {
        "name": "list_files",
        "description": "List files in a directory",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
        },
    },
    {
        "name": "read_file",
        "description": "Read a file's contents",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
]


def main():
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        msg = json.loads(line)
        method = msg.get("method")
        msg_id = msg.get("id")

        if msg_id is None:
            if method == "shutdown":
                break
            continue

        if method == "initialize":
            result = {
                "protocolVersion": "0.1.0",
                "capabilities": {},
                "serverInfo": {"name": "mock-mcp", "version": "0.1.0"},
            }
        elif method == "tools/list":
            result = {"tools": TOOLS}
        else:
            sys.stdout.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "error": {"code": -32601, "message": f"Method not found: {method}"},
                    }
                )
                + "\n"
            )
            sys.stdout.flush()
            continue

        sys.stdout.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": result,
                }
            )
            + "\n"
        )
        sys.stdout.flush()


if __name__ == "__main__":
    main()
