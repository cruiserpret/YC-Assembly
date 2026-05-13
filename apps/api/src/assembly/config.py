from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide configuration. All env vars are prefixed `ASSEMBLY_` except
    LLM provider keys, which keep their canonical names (`ANTHROPIC_API_KEY`,
    `OPENAI_API_KEY`)."""

    # Look for .env in (1) cwd, (2) the repo root relative to apps/api/, and
    # (3) one level up. Pydantic-settings merges these in order so a repo-root
    # .env is found whether the process starts from `apps/api/` or repo root.
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env", "../../.env"),
        env_file_encoding="utf-8",
        env_prefix="ASSEMBLY_",
        extra="ignore",
        case_sensitive=False,
    )

    env: Literal["development", "production", "test"] = "development"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://assembly:assembly_dev@localhost:5433/assembly"
    database_url_sync: str = "postgresql+psycopg://assembly:assembly_dev@localhost:5433/assembly"

    redis_url: str = "redis://localhost:6380/0"

    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")

    llm_primary_provider: Literal["anthropic", "openai"] = "anthropic"
    llm_synthesis_model: str = "claude-opus-4-7"
    llm_roleplay_model: str = "claude-sonnet-4-6"

    cost_soft_usd: float = 0.50
    cost_hard_usd: float = 5.00  # Phase 6.5: full-pipeline runs may exceed $2

    # --- Phase 6.5 simulation infrastructure ---
    simulation_max_concurrency: int = 3
    simulation_default_society_size: int = 6
    enable_aggregation: bool = False  # Phase 7 will flip this default
    expose_raw_state: bool = False    # debug-only endpoint gate

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:3000"
    # Optional CORS regex. When set, ANY Origin that matches this
    # regex (in addition to the cors_origins exact list) passes the
    # preflight. Used in production to allow every Vercel-generated
    # URL for the project (production alias + per-branch previews
    # + per-SHA immutable builds) without enumerating each one.
    cors_allow_regex: str = ""

    # Phase 10B.7 — contact-form email delivery via Resend. When
    # unset, POST /contact returns 503 with a clear "not configured"
    # message and the frontend shows a graceful fallback. Keys are
    # read from env: RESEND_API_KEY, CONTACT_TO_EMAIL,
    # CONTACT_FROM_EMAIL.
    resend_api_key: str | None = Field(default=None, alias="RESEND_API_KEY")
    contact_to_email: str = Field(
        default="team@assemblysimulator.com",
        alias="CONTACT_TO_EMAIL",
    )
    contact_from_email: str = Field(
        default="no-reply@assemblysimulator.com",
        alias="CONTACT_FROM_EMAIL",
    )

    # --- Phase 5.5 retrieval providers ---
    # Off by default. Flip retrieval_enabled=true and pick a search /
    # extraction provider when you want real-world evidence sourcing.
    retrieval_enabled: bool = False
    search_provider: Literal["mock", "tavily", "brave", "serpapi"] = "mock"
    extraction_provider: Literal["mock", "httpx", "firecrawl", "jina"] = "httpx"

    tavily_api_key: str | None = Field(default=None, alias="TAVILY_API_KEY")
    brave_search_api_key: str | None = Field(default=None, alias="BRAVE_SEARCH_API_KEY")
    serpapi_api_key: str | None = Field(default=None, alias="SERPAPI_API_KEY")
    firecrawl_api_key: str | None = Field(default=None, alias="FIRECRAWL_API_KEY")
    jina_api_key: str | None = Field(default=None, alias="JINA_API_KEY")

    # --- Phase 8.5A source-expansion settings ---
    # YouTube Data API v3 — official key only. Comments are pulled via
    # `commentThreads.list`; identity beyond the public commenter
    # display name + comment text is not stored.
    youtube_data_api_key: str | None = Field(
        default=None, alias="YOUTUBE_DATA_API_KEY",
    )
    # Amazon Reviews 2023 LOCAL dataset — there is NO Amazon API key.
    # The dataset is loaded from the local directory referenced here.
    # Live web scraping of Amazon.com is forbidden by both the
    # framework's compliance rules AND Amazon ToS — drift-tested.
    amazon_reviews_2023_dir: str | None = Field(
        default=None, alias="AMAZON_REVIEWS_2023_DIR",
    )
    amazon_reviews_2023_mode: Literal["local", "off"] = Field(
        default="off", alias="AMAZON_REVIEWS_2023_MODE",
    )
    # Comma-separated category list (e.g. "Grocery_and_Gourmet_Food,
    # Health_and_Household") OR the literal string "ALL" to load every
    # category present under the dataset directory.
    amazon_reviews_2023_categories: str | None = Field(
        default=None, alias="AMAZON_REVIEWS_2023_CATEGORIES",
    )

    @property
    def amazon_categories_list(self) -> list[str] | Literal["ALL"]:
        """Parse `AMAZON_REVIEWS_2023_CATEGORIES` into either a list of
        category names or the literal `"ALL"` sentinel. Trims whitespace
        and ignores empty entries.
        """
        raw = (self.amazon_reviews_2023_categories or "").strip()
        if not raw:
            return []
        if raw.upper() == "ALL":
            return "ALL"
        return [c.strip() for c in raw.split(",") if c.strip()]

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
