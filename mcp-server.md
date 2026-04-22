# Creating an MCP Server for AWS Tagging Utilities

This guide explains the step-by-step approach taken to convert the existing AWS tagging tools into a Model Context Protocol (MCP) server using Python and `FastMCP`.

## 1. Choosing the Framework: FastMCP
To build the server, we used **FastMCP**, a high-level Python framework that simplifies the creation of MCP servers. It handles the low-level communication (JSON-RPC over Standard Input/Output) and provides a simple decorator-based API for defining tools.

## 2. Setting Up Dependencies
The project requires the `mcp` and `fastmcp` libraries. These were added to `requirements.txt`:

```text
mcp
fastmcp
```

Install them using:
```bash
pip install mcp fastmcp
```

## 3. Integrating Existing Logic
The core logic for AWS tagging already existed in the `src/` directory as "Lambda-style" handlers (functions that take an `event` and `context`). To avoid duplication, `mcp_server.py` imports these handlers directly.

### Handling Paths
Since `mcp_server.py` is in the root and imports from `src/`, we ensure the root directory is in the Python path:

```python
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
```

## 4. Initializing the Server
A server instance is created with a descriptive name:

```python
from fastmcp import FastMCP

mcp = FastMCP("AWS Tagging Utils")
```

## 5. Defining Tools with `@mcp.tool()`
Each function we want to expose to the AI agent is decorated with `@mcp.tool()`. FastMCP uses the function's docstring and type hints to generate the tool's schema automatically.

### Example: The `read_tags` Tool
We mapped the existing `read_handler` to an MCP tool by:
1. Defining the arguments with clear type hints.
2. Providing a detailed docstring (which the LLM uses to understand when to call the tool).
3. Mapping the tool arguments into the `payload` format expected by the original handler.

```python
@mcp.tool()
def read_tags(
    resource: Optional[str] = None,
    # ... other args
) -> Dict[str, Any]:
    """Read tags from AWS resources..."""
    payload = {
        "resource": resource,
        # ... map other args
    }
    # Call the original handler
    result = read_handler(payload, None)
    return result
```

## 6. Making the Server Executable
Finally, the `mcp.run()` call starts the server in "stdio" mode, which is the standard for MCP communication with clients like Claude Desktop.

```python
if __name__ == "__main__":
    mcp.run()
```

## 7. How to Use the Server
The server is designed to be used by an MCP-compatible client.

## Quick Start

**Prerequisites:** [uv](https://docs.astral.sh/uv/getting-started/installation/) must be installed (provides both `uv` and `uvx`):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Check if uv / uvx is available:** If the MCP server fails to start, verify that `uv` or `uvx` is installed and on your PATH:

```bash
# Check if on PATH and show version
which uv uvx
uv --version
uvx --version
```

If those commands fail, use the **absolute path** in your MCP config instead of `"command": "uv"` or `"command": "uvx"`. Common install locations:

- **Linux:** `~/.local/bin/uv` and `~/.local/bin/uvx` (e.g. `/home/youruser/.local/bin/uv`)
- **macOS:** `~/.local/bin/uv` or `~/.cargo/bin/uv` (if installed via cargo)

Example: `"command": "/home/youruser/.local/bin/uv"` or `"command": "/home/youruser/.local/bin/uvx"`.

---

## MCP configuration

Repository: **https://github.com/NomuPay/mcp-platform**

You can run this MCP server in two ways. Both **uv** and **uvx** can be used; **uv** is commonly used on Linux for local runs with a project path, while **uvx** is convenient for running directly from GitHub without cloning.

### 1. Run from GitHub (no clone)

Use **uvx** so the MCP client fetches and runs the server from the repository. Add to your MCP config (e.g. Cursor `mcp.json`):

```json
{
  "mcpServers": {
    "AWS Tagging Utils": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/kioskOG/aws-tagging-utils",
        "mcp_server",
        "env": {
        "AWS_PROFILE": "dev-account",
        "AWS_REGION": "us-east-2"
      }
        "--transport",
        "stdio"
      ]
    }
  }
}
```

### 2. Run locally after cloning

Clone the repo, then use **uv** with the project path. This is the usual approach on Linux (e.g. Cursor with an explicit `uv` path). Replace `YOUR_WORKSPACE_PATH` with the path to your cloned `aws-tagging-utils`:

```json
{
  "mcpServers": {
    "AWS Tagging Utils": {
      "command": "uv",
      "args": [
        "run",
        "--project",
        "YOUR_WORKSPACE_PATH/aws-tagging-utils",
        "mcp_server",
        "--transport",
        "stdio"
      ]
    }
  }
}
```

**Example (Linux, uv at `~/.local/bin/uv`):**

```json
{
  "mcpServers": {
    "AWS Tagging Utils": {
      "command": "/home/kioskog/.local/bin/uv",
      "args": [
        "run",
        "mcp_server",
        "--transport",
        "stdio"
      ]
    }
  }
}
```

## Summary of Tools Created
- `list_resource_types`: Helps the LLM know which AWS resources are supported.
- `read_tags`: Search for resources by type or tag.
- `write_tags`: Apply or update tags on specific ARNs.
- `apply_governance`: Automatic discovery and tagging.
- `get_tag_report`: Summary status of tagging coverage.
- `sync_tags`: Propagation of tags (e.g., VPC -> Subnets).
