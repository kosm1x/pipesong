from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Telnyx
    telnyx_api_key: str = ""
    telnyx_phone_number: str = ""

    # Deepgram
    deepgram_api_key: str = ""

    # vLLM
    vllm_base_url: str = "http://localhost:8000/v1"
    vllm_model: str = "Qwen/Qwen2.5-7B-Instruct-AWQ"

    # Kokoro TTS
    kokoro_voice: str = "em_alex"
    tts_aggregation_mode: str = "sentence"  # "sentence", "token", or "word"

    # PostgreSQL
    database_url: str = "postgresql+asyncpg://pipesong:pipesong@localhost:5432/pipesong"

    # MinIO
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "pipesong-recordings"

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8080
    app_public_url: str = ""  # e.g. "ws://206.168.83.248:8080" — used in Telnyx webhook if set
    disclosure_text: str = "Esta llamada está siendo grabada para fines de calidad y entrenamiento."

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
