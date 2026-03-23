import io
import logging

from minio import Minio

from pipesong.config import settings

logger = logging.getLogger(__name__)

_client: Minio | None = None


def get_minio_client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=False,
        )
        if not _client.bucket_exists(settings.minio_bucket):
            _client.make_bucket(settings.minio_bucket)
            logger.info("Created MinIO bucket: %s", settings.minio_bucket)
    return _client


def upload_recording(call_id: str, audio_data: bytes, content_type: str = "audio/wav") -> str:
    client = get_minio_client()
    object_name = f"{call_id}.wav"
    client.put_object(
        settings.minio_bucket,
        object_name,
        io.BytesIO(audio_data),
        length=len(audio_data),
        content_type=content_type,
    )
    url = f"http://{settings.minio_endpoint}/{settings.minio_bucket}/{object_name}"
    logger.info("Uploaded recording: %s (%d bytes)", object_name, len(audio_data))
    return url
