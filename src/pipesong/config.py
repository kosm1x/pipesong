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
    disclosure_text: str = "Esta llamada está siendo grabada para fines de calidad y entrenamiento."

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
