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

    litellm_model: str = "copilot/claude-haiku-4.5"
    litellm_api_base: str = ""
    litellm_api_key: str = ""

    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    scan_interval_seconds: int = 0  # 0 disables background scheduler

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
