"""DOM extraction and serialization service.

This module provides a service for extracting and serializing DOM elements
from web pages, making content extraction more reliable and consistent.

Reference: browser-use project's DOM service approach.
"""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


@dataclass
class DOMElement:
    """Represents an extracted DOM element.

    Attributes:
        tag: HTML tag name (lowercase)
        text: Inner text content
        href: Link URL if element is an anchor
        attributes: Dictionary of element attributes
        children: List of child DOMElements
    """

    tag: str
    text: str
    href: Optional[str] = None
    attributes: Dict[str, str] = field(default_factory=dict)
    children: List["DOMElement"] = field(default_factory=list)

    def get_attribute(self, name: str, default: str = "") -> str:
        """Get an attribute value.

        Args:
            name: Attribute name
            default: Default value if not found

        Returns:
            Attribute value or default
        """
        return self.attributes.get(name, default)

    def has_class(self, class_name: str) -> bool:
        """Check if element has a specific class.

        Args:
            class_name: Class name to check

        Returns:
            True if element has the class
        """
        classes = self.attributes.get("class", "").split()
        return class_name in classes

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation.

        Returns:
            Dictionary with element data
        """
        return {
            "tag": self.tag,
            "text": self.text,
            "href": self.href,
            "attributes": self.attributes,
            "children": [child.to_dict() for child in self.children],
        }


class DOMService:
    """Service for DOM extraction and serialization.

    Provides methods for extracting elements, links, and text content
    from web pages using JavaScript evaluation.

    Usage:
        dom = DOMService(page)
        elements = await dom.extract_elements(["a[href*='/article/']"])
        links = await dom.extract_links(filter_pattern=r"/science/article/")
        text = await dom.extract_text_content(".abstract")
    """

    def __init__(self, page: "Page"):
        """Initialize DOM service.

        Args:
            page: Playwright page to extract from
        """
        self.page = page

    async def extract_elements(
        self,
        selectors: List[str],
        include_children: bool = False,
        max_depth: int = 3,
    ) -> List[DOMElement]:
        """Extract elements matching selectors.

        Args:
            selectors: List of CSS selectors
            include_children: Whether to include child elements
            max_depth: Maximum depth for child extraction

        Returns:
            List of extracted DOMElements
        """
        js_code = """
        (args) => {
            const { selectors, includeChildren, maxDepth } = args;
            const results = [];
            const seen = new Set();

            function extractElement(el, depth = 0) {
                const result = {
                    tag: el.tagName.toLowerCase(),
                    text: el.innerText?.trim() || '',
                    href: el.href || null,
                    attributes: {},
                    children: []
                };

                // Extract attributes
                for (const attr of el.attributes) {
                    result.attributes[attr.name] = attr.value;
                }

                // Extract children if requested and within depth limit
                if (includeChildren && depth < maxDepth) {
                    for (const child of el.children) {
                        result.children.push(extractElement(child, depth + 1));
                    }
                }

                return result;
            }

            for (const selector of selectors) {
                try {
                    document.querySelectorAll(selector).forEach(el => {
                        // Avoid duplicates using element's position
                        const key = el.outerHTML.substring(0, 100);
                        if (seen.has(key)) return;
                        seen.add(key);

                        results.push(extractElement(el));
                    });
                } catch (e) {
                    console.error('Selector error:', selector, e);
                }
            }

            return results;
        }
        """

        try:
            raw_results = await self.page.evaluate(
                js_code,
                {
                    "selectors": selectors,
                    "includeChildren": include_children,
                    "maxDepth": max_depth,
                },
            )
            return [self._parse_element(r) for r in raw_results]
        except Exception as e:
            logger.error(f"Error extracting elements: {e}")
            return []

    def _parse_element(self, data: Dict[str, Any]) -> DOMElement:
        """Parse raw element data into DOMElement.

        Args:
            data: Raw element data from JavaScript

        Returns:
            Parsed DOMElement
        """
        return DOMElement(
            tag=data.get("tag", ""),
            text=data.get("text", ""),
            href=data.get("href"),
            attributes=data.get("attributes", {}),
            children=[self._parse_element(c) for c in data.get("children", [])],
        )

    async def extract_links(
        self,
        selector: str = "a[href]",
        filter_pattern: Optional[str] = None,
        exclude_pattern: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Extract all links matching selector.

        Args:
            selector: CSS selector for links
            filter_pattern: Regex pattern to include (optional)
            exclude_pattern: Regex pattern to exclude (optional)

        Returns:
            List of link dictionaries with href, text, title
        """
        js_code = """
        (args) => {
            const { selector, filterPattern, excludePattern } = args;
            const links = [];
            const seen = new Set();
            const filterRegex = filterPattern ? new RegExp(filterPattern) : null;
            const excludeRegex = excludePattern ? new RegExp(excludePattern) : null;

            document.querySelectorAll(selector).forEach(el => {
                const href = el.href;
                if (!href) return;
                if (seen.has(href)) return;

                // Apply filter pattern
                if (filterRegex && !filterRegex.test(href)) return;

                // Apply exclude pattern
                if (excludeRegex && excludeRegex.test(href)) return;

                seen.add(href);
                links.push({
                    href: href,
                    text: el.innerText?.trim() || '',
                    title: el.title || '',
                    target: el.target || ''
                });
            });

            return links;
        }
        """

        try:
            return await self.page.evaluate(
                js_code,
                {
                    "selector": selector,
                    "filterPattern": filter_pattern,
                    "excludePattern": exclude_pattern,
                },
            )
        except Exception as e:
            logger.error(f"Error extracting links: {e}")
            return []

    async def extract_text_content(
        self,
        selector: str,
        join_separator: str = "\n",
        strip_whitespace: bool = True,
    ) -> str:
        """Extract text content from elements.

        Args:
            selector: CSS selector
            join_separator: Separator for joining multiple elements
            strip_whitespace: Whether to strip whitespace

        Returns:
            Extracted text content
        """
        js_code = """
        (args) => {
            const { selector, separator, stripWhitespace } = args;
            const texts = [];

            document.querySelectorAll(selector).forEach(el => {
                let text = el.innerText || '';
                if (stripWhitespace) {
                    text = text.trim();
                }
                if (text) texts.push(text);
            });

            return texts.join(separator);
        }
        """

        try:
            return await self.page.evaluate(
                js_code,
                {
                    "selector": selector,
                    "separator": join_separator,
                    "stripWhitespace": strip_whitespace,
                },
            )
        except Exception as e:
            logger.error(f"Error extracting text content: {e}")
            return ""

    async def extract_table_data(
        self,
        selector: str = "table",
        include_headers: bool = True,
    ) -> List[List[str]]:
        """Extract table data as 2D list.

        Args:
            selector: CSS selector for table
            include_headers: Whether to include header row

        Returns:
            2D list of table cell contents
        """
        js_code = """
        (args) => {
            const { selector, includeHeaders } = args;
            const table = document.querySelector(selector);
            if (!table) return [];

            const rows = [];

            // Extract headers if requested
            if (includeHeaders) {
                const headerRow = table.querySelector('thead tr');
                if (headerRow) {
                    const headers = [];
                    headerRow.querySelectorAll('th, td').forEach(cell => {
                        headers.push(cell.innerText?.trim() || '');
                    });
                    if (headers.length > 0) rows.push(headers);
                }
            }

            // Extract body rows
            table.querySelectorAll('tbody tr, tr').forEach(row => {
                // Skip if this is a header row we already processed
                if (row.parentElement.tagName === 'THEAD') return;

                const cells = [];
                row.querySelectorAll('td, th').forEach(cell => {
                    cells.push(cell.innerText?.trim() || '');
                });
                if (cells.length > 0) rows.push(cells);
            });

            return rows;
        }
        """

        try:
            return await self.page.evaluate(
                js_code,
                {"selector": selector, "includeHeaders": include_headers},
            )
        except Exception as e:
            logger.error(f"Error extracting table data: {e}")
            return []

    async def extract_metadata(self) -> Dict[str, str]:
        """Extract page metadata from meta tags.

        Returns:
            Dictionary of metadata key-value pairs
        """
        js_code = """
        () => {
            const metadata = {};

            // Standard meta tags
            document.querySelectorAll('meta[name], meta[property]').forEach(meta => {
                const name = meta.getAttribute('name') || meta.getAttribute('property');
                const content = meta.getAttribute('content');
                if (name && content) {
                    metadata[name] = content;
                }
            });

            // Title
            const title = document.querySelector('title');
            if (title) {
                metadata['title'] = title.innerText;
            }

            // Canonical URL
            const canonical = document.querySelector('link[rel="canonical"]');
            if (canonical) {
                metadata['canonical'] = canonical.getAttribute('href');
            }

            return metadata;
        }
        """

        try:
            return await self.page.evaluate(js_code)
        except Exception as e:
            logger.error(f"Error extracting metadata: {e}")
            return {}

    async def wait_for_element(
        self,
        selector: str,
        timeout: int = 30000,
        state: str = "visible",
    ) -> bool:
        """Wait for element to appear.

        Args:
            selector: CSS selector
            timeout: Maximum wait time in milliseconds
            state: Element state to wait for ("attached", "visible", "hidden")

        Returns:
            True if element appeared, False if timeout
        """
        try:
            await self.page.wait_for_selector(selector, timeout=timeout, state=state)
            return True
        except Exception:
            return False

    async def count_elements(self, selector: str) -> int:
        """Count elements matching selector.

        Args:
            selector: CSS selector

        Returns:
            Number of matching elements
        """
        try:
            return await self.page.evaluate(
                f"document.querySelectorAll('{selector}').length"
            )
        except Exception:
            return 0

    async def element_exists(self, selector: str) -> bool:
        """Check if element exists.

        Args:
            selector: CSS selector

        Returns:
            True if element exists
        """
        return await self.count_elements(selector) > 0

    async def get_page_html(self, selector: Optional[str] = None) -> str:
        """Get HTML content of page or element.

        Args:
            selector: Optional CSS selector for specific element

        Returns:
            HTML content
        """
        try:
            if selector:
                return await self.page.evaluate(
                    f"document.querySelector('{selector}')?.outerHTML || ''"
                )
            return await self.page.content()
        except Exception as e:
            logger.error(f"Error getting page HTML: {e}")
            return ""

    async def scroll_to_element(self, selector: str) -> bool:
        """Scroll element into view.

        Args:
            selector: CSS selector

        Returns:
            True if successful
        """
        try:
            await self.page.evaluate(
                f"document.querySelector('{selector}')?.scrollIntoView({{behavior: 'smooth', block: 'center'}})"
            )
            return True
        except Exception:
            return False
