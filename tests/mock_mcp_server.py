import json
import os
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
    {
        "name": "crash",
        "description": "Simulate a server crash",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "echo",
        "description": "Echo back the arguments",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
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
            continue

        if method == "ping":
            result = {}
        elif method == "initialize":
            result = {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "serverInfo": {"name": "mock-mcp", "version": "0.1.0"},
            }
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = msg.get("params", {})
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            if tool_name == "crash":
                sys.stdout.flush()
                sys.stderr.flush()
                os._exit(1)

            if tool_name == "echo":
                result = {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(arguments),
                        }
                    ]
                }
            elif tool_name == "list_files":
                result = {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                [
                                    "file1.txt",
                                    "file2.txt",
                                ]
                            ),
                        }
                    ]
                }
            elif tool_name == "read_file":
                path = arguments.get("path", "")
                result = {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Contents of {path}",
                        }
                    ]
                }
            else:
                sys.stdout.write(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": msg_id,
                            "error": {
                                "code": -32601,
                                "message": f"Method not found: {method}",
                            },
                        }
                    )
                    + "\n"
                )
                sys.stdout.flush()
                continue
        else:
            sys.stdout.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "error": {
                            "code": -32601,
                            "message": f"Method not found: {method}",
                        },
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
