"""Data models for papers and search."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field


class PaperSource(str, Enum):
    """Supported academic paper sources."""

    NATURE = "nature"
    SCIENCEDIRECT = "sciencedirect"
    PUBMED = "pubmed"
    ARXIV = "arxiv"
    IEEE = "ieee"
    SPRINGER = "springer"
    GOOGLE_SCHOLAR = "google_scholar"
    UNKNOWN = "unknown"


class Author(BaseModel):
    """Paper author information."""

    name: str
    affiliation: str | None = None
    email: str | None = None
    orcid: str | None = None


class Paper(BaseModel):
    """Academic paper metadata and storage info."""

    # Identifiers
    id: str = Field(default_factory=lambda: str(uuid4()))
    doi: str | None = None
    pmid: str | None = None  # PubMed ID
    arxiv_id: str | None = None

    # Basic metadata
    title: str
    authors: list[Author] = Field(default_factory=list)
    abstract: str | None = None
    journal: str | None = None
    publisher: str | None = None
    published_date: datetime | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None

    # Source info
    source: PaperSource = PaperSource.UNKNOWN
    url: str
    pdf_url: str | None = None

    # AI-generated fields
    categories: list[str] = Field(default_factory=list)
    summary: str | None = None
    keywords: list[str] = Field(default_factory=list)

    # Local storage
    pdf_path: str | None = None
    downloaded_at: datetime | None = None

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    # Extra metadata
    extra: dict[str, Any] = Field(default_factory=dict)

    @computed_field
    @property
    def author_names(self) -> list[str]:
        """Get list of author names."""
        return [a.name for a in self.authors]

    @computed_field
    @property
    def first_author(self) -> str | None:
        """Get first author name."""
        return self.authors[0].name if self.authors else None

    @computed_field
    @property
    def year(self) -> int | None:
        """Get publication year."""
        return self.published_date.year if self.published_date else None

    @computed_field
    @property
    def citation_key(self) -> str:
        """Generate a citation key like 'Smith2024'."""
        author = self.first_author or "Unknown"
        # Get last name
        parts = author.split()
        last_name = parts[-1] if parts else "Unknown"
        year = self.year or "XXXX"
        return f"{last_name}{year}"

    def suggested_filename(self) -> str:
        """Generate a suggested filename for the PDF."""
        # Clean title for filename
        title_words = self.title.split()[:5]
        short_title = "_".join(w for w in title_words if w.isalnum())

        author = self.first_author or "Unknown"
        parts = author.split()
        last_name = parts[-1] if parts else "Unknown"

        year = self.year or "XXXX"

        return f"{year}_{last_name}_{short_title}.pdf"


class SearchQuery(BaseModel):
    """Search query parameters."""

    query: str
    sources: list[PaperSource] = Field(default_factory=list)
    date_from: datetime | None = None
    date_to: datetime | None = None
    max_results: int = 20
    sort_by: str | None = None  # relevance, date, citations
    filters: dict[str, Any] = Field(default_factory=dict)


class SearchResult(BaseModel):
    """Search result container."""

    papers: list[Paper]
    total_count: int
    query: SearchQuery
    search_time: float  # seconds
    source: PaperSource
    has_more: bool = False
    next_page_token: str | None = None


class DownloadResult(BaseModel):
    """Result of a paper download operation."""

    paper_id: str
    success: bool
    pdf_path: str | None = None
    error: str | None = None
    file_size: int | None = None  # bytes


class CategoryResult(BaseModel):
    """Result of AI categorization."""

    paper_id: str
    categories: list[str]
    confidence: float
    reasoning: str | None = None
