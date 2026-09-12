from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.llm.router import EndpointConfig


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"

    # No external DB required to run a research pass in this local edition —
    # results live in-process/SQLite by default. Postgres/Neo4j remain
    # available for anyone who wants persistence across restarts or a real
    # knowledge graph; wire them in api/dependencies.py if you want them.
    postgres_dsn: str = ""
    redis_url: str = ""
    neo4j_uri: str = ""
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""

    sqlite_path: str = "./airp_local.db"

    # Local LLM endpoints (your two PCs). Point these at wherever
    # `ollama serve` is listening on each machine. Leave base_url empty to
    # fall back to MockLLMClient for that endpoint (app boots and produces a
    # templated report with zero setup).
    #
    # To reach Ollama from another machine on your LAN, it must be started
    # with OLLAMA_HOST=0.0.0.0 and the port allowed through the firewall —
    # see docs/LOCAL_SETUP.md.
    llm_reasoning_base_url: str = ""       # e.g. "http://192.168.1.50:11434"  (5070 box)
    llm_reasoning_model: str = "qwen2.5:14b-instruct-q4_K_M"
    llm_extraction_base_url: str = ""      # e.g. "http://192.168.1.51:11434"  (3060 box)
    llm_extraction_model: str = "llama3.1:8b-instruct-q4_K_M"

    default_monte_carlo_seed: int = 42

    cache_ttl_seconds_market_data: int = 60
    cache_ttl_seconds_fundamentals: int = 3600 * 12
    cache_ttl_seconds_filings: int = 3600 * 24 * 7

    # Data provider keys — optional; falls back to Mock* connectors if unset.
    market_data_api_key: str = ""
    sec_edgar_user_agent: str = "AIRP Local research@example.com"
    fred_api_key: str = ""
    news_api_key: str = ""

    def llm_endpoints(self) -> dict[str, EndpointConfig]:
        return {
            "reasoning": EndpointConfig(
                name="reasoning", base_url=self.llm_reasoning_base_url, model=self.llm_reasoning_model,
            ),
            "extraction": EndpointConfig(
                name="extraction", base_url=self.llm_extraction_base_url, model=self.llm_extraction_model,
            ),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
