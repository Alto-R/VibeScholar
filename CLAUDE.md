# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

vibe-paper-search is an MCP server for AI-powered academic paper search, download, and categorization. It uses Playwright for browser automation to access Nature, ScienceDirect, and other academic sites with institutional authentication support.

## Commands

```bash
# Install
pip install -e .
pip install -e ".[dev]"      # with dev dependencies
pip install -e ".[ai]"       # with AI features
playwright install chromium  # required for browser automation

# Development
pytest                       # run tests
pytest tests/test_foo.py -k "test_name"  # single test
mypy vibe_paper_search       # type checking
ruff check vibe_paper_search # linting
ruff check --fix vibe_paper_search # auto-fix

# Run MCP server
python -m vibe_paper_search.mcp.server
```

## Architecture

**MCP Server Layer** (`mcp/server.py`): Entry point exposing 6 tools (search_papers, get_paper_details, download_paper, check_access, login, list_downloaded). Handles tool registration and dispatches to site adapters.

**Site Adapters** (`sites/`): Each academic site has an adapter inheriting from `BaseSiteAdapter`. Adapters implement search, get_paper_details, download_pdf, and check_access. Currently: NatureAdapter, ScienceDirectAdapter.

**Browser Session** (`browser/session.py`): Playwright wrapper with proxy auto-detection (v2ray/clash ports), storage state persistence for auth, and session management via `SessionManager`.

**Auth Watchdog** (`browser/watchdogs/auth_watchdog.py`): Detects login pages and paywalls using regex patterns, manages per-site authentication state files.

**Data Models** (`papers/models.py`): Pydantic v2 models - `Paper`, `Author`, `SearchQuery`, `SearchResult`, `DownloadResult`.

## Key Patterns

- All browser operations are async (Playwright async API)
- Site adapters use rate limiting via `_rate_limit()` method
- Storage state saved to `~/.vibe-paper-search/auth/{session}_{site}_auth.json`
- Config via pydantic-settings with `VIBE_` env prefix

## Adding a New Site Adapter

1. Create `sites/newsite.py` inheriting from `BaseSiteAdapter`
2. Implement: `search()`, `get_paper_details()`, `download_pdf()`, `check_access()`
3. Add to `sites/__init__.py` exports
4. Register in `mcp/server.py` `get_adapter()` function


## python path

d:/anaconda/envs/vibepaper/python.exe
always use this path to use python