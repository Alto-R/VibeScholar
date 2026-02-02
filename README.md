# Vibe Paper Search

AI-powered academic paper search, download, and categorization tool.

## Features

- **Multi-source Search**: Search across Nature, ScienceDirect, and more
- **Institutional Authentication**: Supports Shibboleth, EZProxy, and other institutional login methods
- **PDF Download**: Download papers with proper authentication
- **MCP Integration**: Use with Claude, Cursor, and other MCP-compatible AI tools

## Installation

```bash
# Clone the repository
cd vibe-paper-search

# Install with pip
pip install -e .

# Install Playwright browsers
playwright install chromium
```

## Configuration

Create a `.env` file or set environment variables:

```bash
# Storage paths
VIBE_DATA_DIR=~/.vibe-paper-search
VIBE_PAPERS_DIR=~/Papers

# Browser settings
VIBE_HEADLESS=true
VIBE_BROWSER_TYPE=chromium

# Proxy (auto-detected if not set)
VIBE_PROXY_URL=http://127.0.0.1:7897

# AI settings (optional)
VIBE_OPENAI_API_KEY=sk-...
VIBE_ANTHROPIC_API_KEY=sk-ant-...
```

## Usage

### As MCP Server

Add to your Claude/Cursor MCP configuration:

```json
{
  "mcpServers": {
    "vibe-paper-search": {
      "command": "python",
      "args": ["-m", "vibe_paper_search.mcp.server"]
    }
  }
}
```

### Available MCP Tools

1. **search_papers** - Search for academic papers
   ```
   query: "machine learning protein structure"
   sources: ["nature", "sciencedirect"]
   max_results: 10
   ```

2. **get_paper_details** - Get detailed paper information
   ```
   url: "https://www.nature.com/articles/..."
   ```

3. **download_paper** - Download PDF (requires authentication)
   ```
   url: "https://www.nature.com/articles/..."
   filename: "optional_custom_name.pdf"
   ```

4. **check_access** - Check if you have access to a paper
   ```
   url: "https://www.nature.com/articles/..."
   ```

5. **login** - Open browser for institutional login
   ```
   site: "nature" | "sciencedirect"
   ```

6. **list_downloaded** - List all downloaded papers

### Institutional Login

For accessing paywalled content:

1. Use the `login` tool to open a browser window
2. Complete your institutional login (Shibboleth, EZProxy, etc.)
3. Your session will be saved automatically
4. Subsequent searches and downloads will use your authenticated session

## Project Structure

```
vibe_paper_search/
├── config.py           # Configuration management
├── mcp/                # MCP Server
│   └── server.py       # MCP tools implementation
├── browser/            # Browser automation
│   ├── session.py      # Playwright session management
│   └── watchdogs/      # Auth and download handlers
├── sites/              # Academic site adapters
│   ├── base.py         # Base adapter interface
│   ├── nature.py       # Nature.com adapter
│   └── sciencedirect.py # ScienceDirect adapter
├── papers/             # Paper data models
│   └── models.py       # Pydantic models
└── ai/                 # AI features (coming soon)
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Type checking
mypy vibe_paper_search

# Linting
ruff check vibe_paper_search
```

## License

MIT
