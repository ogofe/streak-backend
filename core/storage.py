import hmac
import time
from dataclasses import dataclass
from hashlib import sha256

from django.conf import settings


@dataclass(frozen=True)
class PresignedUpload:
    method: str
    url: str
    headers: dict
    expires_at: int
    provider: str


def build_presigned_upload(*, object_key: str, mime_type: str, size_bytes: int) -> PresignedUpload:
    if settings.UPLOAD_STORAGE_BACKEND == "s3":
        return _s3_presigned_upload(object_key=object_key, mime_type=mime_type, size_bytes=size_bytes)
    return _local_presigned_upload(object_key=object_key, mime_type=mime_type, size_bytes=size_bytes)


def _s3_presigned_upload(*, object_key: str, mime_type: str, size_bytes: int) -> PresignedUpload:
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("boto3 is required when UPLOAD_STORAGE_BACKEND=s3.") from exc

    client = boto3.client("s3", region_name=settings.AWS_REGION)
    url = client.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": settings.AWS_S3_BUCKET,
            "Key": object_key,
            "ContentType": mime_type,
        },
        ExpiresIn=settings.UPLOAD_SIGNING_TTL_SECONDS,
        HttpMethod="PUT",
    )
    return PresignedUpload(
        method="PUT",
        url=url,
        headers={"Content-Type": mime_type},
        expires_at=int(time.time()) + settings.UPLOAD_SIGNING_TTL_SECONDS,
        provider="s3",
    )


def _local_presigned_upload(*, object_key: str, mime_type: str, size_bytes: int) -> PresignedUpload:
    expires_at = int(time.time()) + settings.UPLOAD_SIGNING_TTL_SECONDS
    payload = f"{settings.AWS_S3_BUCKET}:{object_key}:{mime_type}:{size_bytes}:{expires_at}"
    signature = hmac.new(settings.SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), sha256).hexdigest()
    return PresignedUpload(
        method="PUT",
        url=f"https://local-upload.invalid/{settings.AWS_S3_BUCKET}/{object_key}?expires={expires_at}&signature={signature}",
        headers={
            "Content-Type": mime_type,
            "x-streak-upload-signature": signature,
            "x-streak-upload-expires-at": str(expires_at),
        },
        expires_at=expires_at,
        provider="local",
    )
