from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    postgres_dsn: str = "postgresql://qlik:qlik@localhost:5432/qlik_lineage"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password"

    # --- Qlik `/custom` virtual proxy (header authentication) ----------------
    # All access goes through the virtual proxy over HTTPS. No NTLM, no direct
    # QRS :4242, no client certificates. Authentication is via HTTP headers.
    qlik_base_url: str = "https://10.221.11.6/custom"   # MUST include the /custom prefix
    qlik_xrf_key: str = "1234567890abcdef"              # X-Qlik-Xrfkey header + xrfkey query param
    qlik_user: str = "CORPORATE\\srv-qlik"              # X-Qlik-User header (DOMAIN\\user)
    qlik_verify_ssl: bool = False
    qlik_timeout_seconds: int = 60         # per-request QRS HTTP read timeout

    # Engine WebSocket — direct mutual-TLS connection to the engine host (port
    # 4747), authenticated with a client certificate (client.pfx + root.cer) and
    # the X-Qlik-User header. This does NOT go through the /custom virtual proxy.
    qlik_engine_host: str = "ms16-p-0295.dcsg.com"
    qlik_engine_port: int = 4747
    qlik_engine_path: str = "/app"          # yields wss://<host>:4747/app/<APP_ID>
    qlik_engine_user: str = "UserDirectory=corporate; UserId=srv-qlik"  # X-Qlik-User header
    qlik_client_pfx: str = ""               # path to client.pfx (blank => backend/certs/client.pfx)
    qlik_root_cer: str = ""                 # path to root.cer  (blank => backend/certs/root.cer)
    qlik_pfx_password: str = ""             # password for client.pfx
    qlik_engine_timeout_seconds: int = 300  # WS connect/recv timeout; big apps are slow to OpenDoc
    qlik_export_scripts: bool = False
    qlik_export_scripts_dir: str = ""
    qlik_max_concurrent_apps: int = 1       # 1 = sequential (safe default)

    litellm_model: str = "github_copilot/gpt-4o"
    litellm_api_base: str = ""
    litellm_api_key: str = ""

    # --- Output token caps ---------------------------------------------------
    # Copilot enforces a hard prompt-token limit (64k on gpt-4o) and this system
    # has already breached it once at 185,906 tokens. Output was previously
    # unbounded because no max_tokens was ever set. These caps bound both ends.
    llm_max_output_tokens: int = 800          # normal chat answers
    docgen_max_output_tokens: int = 8000      # documentation: 7 sections needs room
    docgen_max_evidence_tokens: int = 12000   # ceiling on the evidence pack we send
    docgen_output_dir: str = "generated_docs" # where .md files are written
    docgen_max_retries: int = 5               # retries after the initial 429 response
    docgen_retry_base_seconds: float = 2.0     # exponential-backoff starting delay
    docgen_retry_max_seconds: float = 60.0     # cap for fallback backoff delays

    # Documentation uses a SEPARATE model from chat. Measured on this Copilot
    # subscription: asked for 8,000 output tokens, gpt-4o returned only 1,610
    # while claude-haiku-4.5 returned the full 8,000 - roughly 5x more usable
    # document per call, with a 200k context window instead of 128k. Chat answers
    # are short so they stay on the cheaper default. Blank => use litellm_model.
    docgen_model: str = "github_copilot/claude-haiku-4.5"

    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    scan_interval_seconds: int = 0  # 0 disables background scheduler

    @property
    def effective_docgen_model(self) -> str:
        """Model used for documentation, falling back to the chat model if unset."""
        return self.docgen_model.strip() or self.litellm_model

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
