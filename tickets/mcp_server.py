"""Run the support-ticket MCP server over local stdio."""

import os


def main() -> None:
    """Initialize Django, then serve MCP messages on stdin/stdout."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    import django

    django.setup()

    from .mcp_tools import mcp

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
