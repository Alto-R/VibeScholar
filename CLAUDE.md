# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**vibescholar** 是一个 MCP Server，用于 AI 驱动的学术论文搜索、下载和分类。使用 Playwright 进行浏览器自动化，支持访问 Nature、ScienceDirect 等需要机构认证的学术网站。

### 核心功能

- 跨多个学术数据库搜索论文 (Nature, ScienceDirect)
- 自动处理机构认证和登录状态持久化
- 下载 PDF 并自动命名
- 处理 CAPTCHA 和 Cookie 同意弹窗 (支持后台监控)
- 支持 Chrome/Edge 浏览器切换 (默认 Edge)

## Python 环境

```bash
# 始终使用此 Python 路径
d:/anaconda/envs/vibepaper/python.exe
```

## 命令

```bash
# 安装
pip install -e .
pip install -e ".[dev]"      # 开发依赖
pip install -e ".[ai]"       # AI 功能
playwright install chromium  # 浏览器自动化必需

# 开发
pytest                       # 运行测试
mypy vibescholar             # 类型检查
ruff check vibescholar       # 代码检查
ruff check --fix vibescholar # 自动修复

# 运行 MCP 服务器
d:/anaconda/envs/vibepaper/python.exe -m vibescholar.mcp.server
```

## 目录结构

```text
vibescholar/
├── __init__.py
├── config.py                    # 配置管理 (pydantic-settings)
│
├── mcp/                         # MCP Server 接口
│   ├── __init__.py
│   └── server.py               # MCP 服务器实现，6个工具
│
├── browser/                     # 浏览器自动化层
│   ├── __init__.py
│   ├── session.py              # 浏览器会话管理 (Playwright)
│   ├── captcha_handler.py      # CAPTCHA 处理 (全局锁机制)
│   ├── dom_service.py          # DOM 操作服务
│   └── watchdogs/
│       ├── __init__.py
│       ├── auth_watchdog.py    # 认证状态和付费墙检测
│       ├── captcha_watchdog.py # CAPTCHA 页面状态检测
│       └── cookie_watchdog.py  # Cookie 同意弹窗监控
│
├── sites/                       # 学术网站适配器
│   ├── __init__.py
│   ├── base.py                 # 基础适配器接口 (BaseSiteAdapter)
│   ├── nature.py               # Nature 适配器
│   └── sciencedirect.py        # ScienceDirect 适配器
│
├── papers/                      # 论文数据模型
│   ├── __init__.py
│   └── models.py               # Pydantic 模型定义
│
├── ai/                          # AI 集成层 (待实现)
│   └── __init__.py
│
└── utils/                       # 工具函数
    └── __init__.py

tests/
├── test_download_nature.py     # Nature 下载测试
├── test_download_sciencedirect.py # ScienceDirect 下载测试
└── run_download.py             # 下载测试运行脚本
```

## 核心模块详解

### 1. 配置管理 (`config.py`)

使用 pydantic-settings，环境变量前缀 `VIBE_`。

```python
class Settings(BaseSettings):
    # 存储路径
    data_dir: Path              # 默认 ~/.vibescholar
    papers_dir: Path            # PDF 存储目录 (默认 data_dir/papers)

    # 浏览器设置
    browser: Literal["chrome", "edge"]  # 默认 "edge"
    headless: bool              # 默认 False (显示浏览器窗口)
    user_data_dir: Path | None  # Chrome 用户数据目录

    # 代理设置
    proxy_url: str | None       # HTTP 代理
    auto_detect_proxy: bool     # 自动检测 v2ray/clash

    # AI 设置
    openai_api_key: str | None
    anthropic_api_key: str | None
    llm_provider: Literal["openai", "anthropic"]
    llm_model: str              # 默认 "gpt-4o-mini"

    # MCP Server 设置
    mcp_host: str               # 默认 "localhost"
    mcp_port: int               # 默认 8765

    # 搜索设置
    default_max_results: int    # 默认 20
    search_timeout: int         # 默认 60 秒
    download_timeout: int       # 默认 120 秒
```

关键属性:

- `storage_state_dir`: `~/.vibescholar/auth/` - 认证状态存储
- `database_path`: `~/.vibescholar/papers.db` - SQLite 数据库
- `logs_dir`: `~/.vibescholar/logs/` - 日志目录

### 2. 浏览器会话 (`browser/session.py`)

Playwright 封装，支持 Chrome/Edge。

```python
# 浏览器路径配置
BROWSER_PATHS = {
    "chrome": {
        "win32": ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", ...],
        "darwin": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
        "linux": ["/usr/bin/google-chrome", ...],
    },
    "edge": {
        "win32": ["C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe", ...],
        ...
    },
}

# 反自动化检测参数
BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
]
```

关键类:

- `BrowserSession`: 单个浏览器会话，管理页面和存储状态
- `SessionManager`: 增强型会话管理器，支持超时清理和 LRU 淘汰
- `browser_session()`: 异步上下文管理器

**SessionManager 特性:**

- 每站点会话管理，自动复用
- 空闲会话自动清理 (默认 10 分钟超时)
- 最大会话数限制 (默认 5 个)，LRU 淘汰
- 活动追踪和会话刷新

关键功能:

- `find_browser(browser_type)`: 查找已安装的浏览器
- `detect_proxy()`: 自动检测本地代理 (v2ray/clash 端口)
- 存储状态持久化: `{session_id}_storage.json`

### 3. 浏览器辅助模块

#### CaptchaHandler (`browser/captcha_handler.py`)

- 全局锁机制处理 CAPTCHA
- 打开可见浏览器窗口让用户解决
- 其他请求等待 CAPTCHA 解决完成

#### DOMService (`browser/dom_service.py`)

- DOM 操作封装
- 元素查找和交互

#### Watchdogs (`browser/watchdogs/`)

| 模块 | 功能 |
| ---- | ---- |
| `AuthWatchdog` | 认证状态检测、付费墙检测、PDF 可用性检测 |
| `CaptchaWatchdog` | CAPTCHA 页面状态检测 (PageState 枚举) |
| `CookieWatchdog` | Cookie 同意弹窗后台监控和自动处理 |

### 4. 网站适配器 (`sites/`)

#### 基类 (`base.py`)

```python
class BaseSiteAdapter(ABC):
    name: str                   # 网站名称
    source: PaperSource         # 数据源枚举
    base_url: str               # 基础 URL
    requires_auth: bool         # 是否需要认证
    requests_per_minute: int    # 速率限制

    # 内置服务
    auth_watchdog: AuthWatchdog
    cookie_watchdog: CookieWatchdog
    captcha_handler: CaptchaHandler
    dom_service: DOMService

    @abstractmethod
    async def search(query, max_results, **kwargs) -> SearchResult

    @abstractmethod
    async def get_paper_details(url) -> Paper

    @abstractmethod
    async def download_pdf(paper, save_path) -> DownloadResult

    async def check_access(url) -> bool
    async def handle_captcha(url) -> bool
    async def wait_for_user_auth(timeout, check_interval) -> bool
    async def find_pdf_link(extra_selectors) -> tuple[str | None, any]
    async def download_pdf_via_js(pdf_url, save_path) -> DownloadResult
```

#### Nature 适配器 (`nature.py`)

- 搜索 URL: `https://www.nature.com/search`
- 使用 JavaScript 提取搜索结果
- 选择器: `a[data-track-action="view article"]`

#### ScienceDirect 适配器 (`sciencedirect.py`)

**特殊处理:**

- 模拟人类搜索行为 (导航到首页 → 输入搜索框 → 按 Enter)
- CAPTCHA/机器人检测处理
- Cookie 同意弹窗处理

```python
class PageState(str, Enum):
    CAPTCHA = "captcha"
    SEARCH_RESULTS = "search_results"
    NO_RESULTS = "no_results"
    ARTICLE_PAGE = "article_page"
    LOGIN_REQUIRED = "login_required"
    UNKNOWN = "unknown"
```

### 5. 数据模型 (`papers/models.py`)

```python
class PaperSource(str, Enum):
    NATURE = "nature"
    SCIENCEDIRECT = "sciencedirect"
    PUBMED = "pubmed"
    ARXIV = "arxiv"
    IEEE = "ieee"
    SPRINGER = "springer"
    GOOGLE_SCHOLAR = "google_scholar"
    UNKNOWN = "unknown"

class Author(BaseModel):
    name: str
    affiliation: str | None
    email: str | None
    orcid: str | None

class Paper(BaseModel):
    # 标识符
    id: str                     # UUID
    doi: str | None
    pmid: str | None
    arxiv_id: str | None

    # 基本元数据
    title: str
    authors: list[Author]
    abstract: str | None
    journal: str | None
    publisher: str | None
    published_date: datetime | None
    volume: str | None
    issue: str | None
    pages: str | None

    # 来源信息
    source: PaperSource
    url: str
    pdf_url: str | None

    # AI 生成字段
    categories: list[str]
    summary: str | None
    keywords: list[str]

    # 本地存储
    pdf_path: str | None
    downloaded_at: datetime | None

    # 时间戳
    created_at: datetime
    updated_at: datetime

    # 扩展元数据
    extra: dict[str, Any]

    # 计算属性
    @computed_field
    def author_names(self) -> list[str]
    def first_author(self) -> str | None
    def year(self) -> int | None
    def citation_key(self) -> str  # "Smith2024"
    def suggested_filename(self) -> str  # "2024_Smith_Title.pdf"

class SearchQuery(BaseModel):
    query: str
    sources: list[PaperSource]
    date_from: datetime | None
    date_to: datetime | None
    max_results: int
    sort_by: str | None
    filters: dict[str, Any]

class SearchResult(BaseModel):
    papers: list[Paper]
    total_count: int
    query: SearchQuery
    search_time: float
    source: PaperSource
    has_more: bool
    next_page_token: str | None

class DownloadResult(BaseModel):
    paper_id: str
    success: bool
    pdf_path: str | None
    error: str | None
    file_size: int | None

class CategoryResult(BaseModel):
    paper_id: str
    categories: list[str]
    confidence: float
    reasoning: str | None
```

### 6. MCP Server (`mcp/server.py`)

提供 6 个工具:

| 工具 | 描述 |
| ---- | ---- |
| `search_papers` | 搜索论文 (query, sources, max_results) |
| `get_paper_details` | 获取论文详情 (url, source) |
| `download_paper` | 下载 PDF (url, filename) |
| `check_access` | 检查访问权限 (url) |
| `login` | 打开浏览器进行手动登录 (site) |
| `list_downloaded` | 列出已下载论文 (category) |

## 关键模式

### 异步操作

所有浏览器操作都是异步的 (Playwright async API)

### 速率限制

适配器使用 `_rate_limit()` 方法控制请求频率

### 存储状态持久化

- 会话级: `{session_id}_storage.json`
- 共享级: `shared_storage.json`

### 人类行为模拟 (ScienceDirect)

```python
# 1. 导航到首页
await self._navigate(self.base_url)

# 2. 找到搜索框并输入
search_input = await page.wait_for_selector('input[type="search"]')
await search_input.type(query, delay=50)  # 模拟打字延迟

# 3. 按 Enter 提交
await search_input.press("Enter")

# 4. 等待 URL 变化
await page.wait_for_url(lambda url: "/search" in url)
```

## 添加新网站适配器

1. 创建 `sites/newsite.py`，继承 `BaseSiteAdapter`
2. 实现必需方法:
   - `search()`: 搜索论文
   - `get_paper_details()`: 获取详情
   - `download_pdf()`: 下载 PDF
   - `check_access()`: 检查权限
3. 添加到 `sites/__init__.py` 导出
4. 在 `mcp/server.py` 的 `get_adapter()` 中注册

## 测试示例

```python
# 测试 ScienceDirect 搜索
import asyncio
from vibescholar.browser.session import BrowserSession
from vibescholar.sites.sciencedirect import ScienceDirectAdapter

async def test():
    session = BrowserSession(
        session_id='sciencedirect',
        headless=False,
        browser_type='edge',  # 或 'chrome'
    )
    await session.start()

    adapter = ScienceDirectAdapter(session)
    result = await adapter.search('reinforcement learning', max_results=5)

    for paper in result.papers:
        print(f"{paper.title} - {paper.url}")

    await session.stop()

asyncio.run(test())
```

## 常见问题

### CAPTCHA 处理

ScienceDirect 会触发 CAPTCHA，适配器会等待用户手动解决 (headless=False 时)。使用 `CaptchaHandler` 的全局锁机制确保同一时间只有一个 CAPTCHA 处理窗口。

### Cookie 弹窗

`CookieWatchdog` 支持两种模式:

- `handle_once()`: 一次性处理
- `start()/stop()`: 后台持续监控

### 浏览器选择

- `chrome`: 使用已安装的 Chrome
- `edge`: 使用已安装的 Edge (默认)

### 代理检测

自动检测本地代理端口:

- 7890 (Clash HTTP)
- 10809 (v2ray HTTP)
- 7891 (Clash SOCKS5)
- 10808 (v2ray SOCKS5)
- 1080 (通用 SOCKS5)
