"""MCP Server implementation for vibe-paper-search."""

import asyncio
import logging
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from ..browser import BrowserSession, session_manager
from ..config import settings
from ..papers import Paper, PaperSource, SearchResult
from ..sites import NatureAdapter, ScienceDirectAdapter

logger = logging.getLogger(__name__)

# Create MCP server
server = Server("vibe-paper-search")

# Active adapters cache
_adapters: dict[str, Any] = {}


async def get_adapter(source: PaperSource, session: BrowserSession):
    """Get or create an adapter for a source."""
    key = f"{source.value}_{session.session_id}"
    if key not in _adapters:
        if source == PaperSource.NATURE:
            _adapters[key] = NatureAdapter(session)
        elif source == PaperSource.SCIENCEDIRECT:
            _adapters[key] = ScienceDirectAdapter(session)
        else:
            raise ValueError(f"Unsupported source: {source}")
    return _adapters[key]


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available MCP tools."""
    return [
        Tool(
            name="search_papers",
            description="Search for academic papers across Nature and ScienceDirect",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (keywords or natural language question)",
                    },
                    "sources": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["nature", "sciencedirect"],
                        },
                        "description": "Data sources to search (default: all)",
                        "default": [],
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results per source",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_paper_details",
            description="Get detailed information about a specific paper",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL of the paper",
                    },
                    "source": {
                        "type": "string",
                        "enum": ["nature", "sciencedirect"],
                        "description": "Source of the paper (auto-detected if not provided)",
                    },
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="download_paper",
            description="Download PDF of a paper (requires institutional access)",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL of the paper",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Custom filename (optional, auto-generated if not provided)",
                    },
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="check_access",
            description="Check if you have access to download a paper's PDF",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL of the paper to check",
                    },
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="login",
            description="Open browser for manual institutional login",
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {
                        "type": "string",
                        "enum": ["nature", "sciencedirect"],
                        "description": "Site to login to",
                    },
                },
                "required": ["site"],
            },
        ),
        Tool(
            name="list_downloaded",
            description="List all downloaded papers",
            inputSchema={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Filter by category (optional)",
                    },
                },
            },
        ),
    ]


def detect_source(url: str) -> PaperSource:
    """Detect paper source from URL."""
    url_lower = url.lower()
    if "nature.com" in url_lower:
        return PaperSource.NATURE
    elif "sciencedirect.com" in url_lower or "elsevier.com" in url_lower:
        return PaperSource.SCIENCEDIRECT
    else:
        raise ValueError(f"Unknown source for URL: {url}")


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""
    try:
        if name == "search_papers":
            return await handle_search(arguments)
        elif name == "get_paper_details":
            return await handle_get_details(arguments)
        elif name == "download_paper":
            return await handle_download(arguments)
        elif name == "check_access":
            return await handle_check_access(arguments)
        elif name == "login":
            return await handle_login(arguments)
        elif name == "list_downloaded":
            return await handle_list_downloaded(arguments)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as e:
        logger.exception(f"Tool {name} failed")
        return [TextContent(type="text", text=f"Error: {str(e)}")]


async def handle_search(arguments: dict) -> list[TextContent]:
    """Handle search_papers tool."""
    query = arguments["query"]
    sources = arguments.get("sources", [])
    max_results = arguments.get("max_results", 10)

    # Default to all sources if none specified
    if not sources:
        sources = ["nature", "sciencedirect"]

    # Get browser session
    session = await session_manager.get_session(headless=True)

    all_results = []
    errors = []

    for source_name in sources:
        try:
            source = PaperSource(source_name)
            adapter = await get_adapter(source, session)
            result = await adapter.search(query, max_results=max_results)
            all_results.append(result)
        except Exception as e:
            errors.append(f"{source_name}: {str(e)}")
            logger.error(f"Search failed for {source_name}: {e}")

    # Format results
    output_lines = [f"## Search Results for: {query}\n"]

    for result in all_results:
        output_lines.append(f"\n### {result.source.value.title()} ({len(result.papers)} results)\n")
        for i, paper in enumerate(result.papers, 1):
            authors = ", ".join(paper.author_names[:3])
            if len(paper.author_names) > 3:
                authors += " et al."
            output_lines.append(f"{i}. **{paper.title}**")
            output_lines.append(f"   - Authors: {authors}")
            if paper.journal:
                output_lines.append(f"   - Journal: {paper.journal}")
            if paper.year:
                output_lines.append(f"   - Year: {paper.year}")
            output_lines.append(f"   - URL: {paper.url}")
            if paper.doi:
                output_lines.append(f"   - DOI: {paper.doi}")
            output_lines.append("")

    if errors:
        output_lines.append("\n### Errors\n")
        for error in errors:
            output_lines.append(f"- {error}")

    return [TextContent(type="text", text="\n".join(output_lines))]


async def handle_get_details(arguments: dict) -> list[TextContent]:
    """Handle get_paper_details tool."""
    url = arguments["url"]
    source_name = arguments.get("source")

    if source_name:
        source = PaperSource(source_name)
    else:
        source = detect_source(url)

    session = await session_manager.get_session(headless=True)
    adapter = await get_adapter(source, session)
    paper = await adapter.get_paper_details(url)

    # Format output
    output_lines = [
        f"## {paper.title}\n",
        f"**Authors:** {', '.join(paper.author_names)}",
        f"**Journal:** {paper.journal or 'N/A'}",
        f"**Year:** {paper.year or 'N/A'}",
        f"**DOI:** {paper.doi or 'N/A'}",
        f"**URL:** {paper.url}",
    ]

    if paper.abstract:
        output_lines.append(f"\n### Abstract\n{paper.abstract}")

    if paper.pdf_url:
        output_lines.append(f"\n**PDF URL:** {paper.pdf_url}")

    return [TextContent(type="text", text="\n".join(output_lines))]


async def handle_download(arguments: dict) -> list[TextContent]:
    """Handle download_paper tool."""
    url = arguments["url"]
    filename = arguments.get("filename")

    source = detect_source(url)
    session = await session_manager.get_session(headless=True)
    adapter = await get_adapter(source, session)

    # Get paper details first
    paper = await adapter.get_paper_details(url)

    # Generate filename if not provided
    if not filename:
        filename = paper.suggested_filename()

    # Ensure papers directory exists
    settings.ensure_dirs()
    save_path = str(settings.papers_dir / filename)

    # Download
    result = await adapter.download_pdf(paper, save_path)

    if result.success:
        return [TextContent(
            type="text",
            text=f"Successfully downloaded: {filename}\nPath: {result.pdf_path}\nSize: {result.file_size} bytes",
        )]
    else:
        return [TextContent(
            type="text",
            text=f"Download failed: {result.error}\n\nYou may need to login first using the 'login' tool.",
        )]


async def handle_check_access(arguments: dict) -> list[TextContent]:
    """Handle check_access tool."""
    url = arguments["url"]

    source = detect_source(url)
    session = await session_manager.get_session(headless=True)
    adapter = await get_adapter(source, session)

    has_access = await adapter.check_access(url)

    if has_access:
        return [TextContent(type="text", text="✓ You have access to download this paper.")]
    else:
        return [TextContent(
            type="text",
            text="✗ Access denied. You may need institutional login.\n\nUse the 'login' tool to authenticate.",
        )]


async def handle_login(arguments: dict) -> list[TextContent]:
    """Handle login tool - opens visible browser for manual login."""
    site = arguments["site"]

    # Get session with visible browser
    session = await session_manager.get_session(headless=False)

    if site == "nature":
        url = "https://www.nature.com"
    elif site == "sciencedirect":
        url = "https://www.sciencedirect.com"
    else:
        return [TextContent(type="text", text=f"Unknown site: {site}")]

    await session.goto(url)

    return [TextContent(
        type="text",
        text=f"Browser opened for {site}. Please complete your institutional login.\n\n"
        "After logging in, your session will be saved automatically.\n"
        "You can then use search and download tools with your authenticated session.",
    )]


async def handle_list_downloaded(arguments: dict) -> list[TextContent]:
    """Handle list_downloaded tool."""
    from pathlib import Path

    papers_dir = settings.papers_dir
    if not papers_dir.exists():
        return [TextContent(type="text", text="No papers downloaded yet.")]

    pdf_files = list(papers_dir.glob("**/*.pdf"))

    if not pdf_files:
        return [TextContent(type="text", text="No papers downloaded yet.")]

    output_lines = [f"## Downloaded Papers ({len(pdf_files)} files)\n"]

    for pdf_file in sorted(pdf_files, key=lambda p: p.stat().st_mtime, reverse=True):
        size_mb = pdf_file.stat().st_size / (1024 * 1024)
        output_lines.append(f"- {pdf_file.name} ({size_mb:.1f} MB)")

    return [TextContent(type="text", text="\n".join(output_lines))]


async def run_server():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    """Entry point for MCP server."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
