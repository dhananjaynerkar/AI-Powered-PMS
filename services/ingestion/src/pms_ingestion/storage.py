"""Private immutable object-store boundary backed by MinIO."""

from __future__ import annotations

from collections.abc import Iterable
from io import BytesIO
from typing import Protocol

from minio import Minio
from minio.commonconfig import ENABLED
from minio.error import MinioException
from minio.versioningconfig import VersioningConfig
from pms_common.settings import Settings
from urllib3.exceptions import HTTPError

from pms_ingestion.models import ObjectWrite


class ObjectStorageError(RuntimeError):
    """Raised when private object storage cannot satisfy an operation."""


class ObjectStore(Protocol):
    def ensure_buckets(self, bucket_names: Iterable[str]) -> tuple[str, ...]: ...

    def put_immutable(
        self,
        *,
        bucket_name: str,
        object_key: str,
        content: bytes,
        mime_type: str,
        checksum_sha256: str,
    ) -> ObjectWrite: ...

    def get(
        self,
        *,
        bucket_name: str,
        object_key: str,
        object_version: str | None,
    ) -> bytes: ...


def configured_bucket_names(settings: Settings) -> tuple[str, ...]:
    return (
        settings.minio_bucket_raw,
        settings.minio_bucket_canonical,
        settings.minio_bucket_derived,
        settings.minio_bucket_models,
        settings.minio_bucket_evaluation,
    )


class MinioObjectStore:
    """Use service credentials internally; buckets remain non-public."""

    def __init__(self, settings: Settings) -> None:
        if settings.minio_access_key is None or settings.minio_secret_key is None:
            raise ObjectStorageError("MinIO credentials are not configured")
        access_key = settings.minio_access_key.get_secret_value().strip()
        secret_key = settings.minio_secret_key.get_secret_value().strip()
        if not access_key or not secret_key:
            raise ObjectStorageError("MinIO credentials are not configured")
        self._object_lock_enabled = settings.minio_object_lock_enabled
        self._client = Minio(
            endpoint=settings.minio_endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=settings.minio_secure,
            region=settings.minio_region,
        )

    def ensure_buckets(self, bucket_names: Iterable[str]) -> tuple[str, ...]:
        ensured: list[str] = []
        try:
            for bucket_name in bucket_names:
                if not self._client.bucket_exists(bucket_name):
                    self._client.make_bucket(
                        bucket_name,
                        object_lock=self._object_lock_enabled,
                    )
                self._client.set_bucket_versioning(
                    bucket_name,
                    VersioningConfig(ENABLED),
                )
                ensured.append(bucket_name)
        except (MinioException, HTTPError, OSError) as error:
            raise ObjectStorageError("MinIO bucket initialization failed") from error
        return tuple(ensured)

    def put_immutable(
        self,
        *,
        bucket_name: str,
        object_key: str,
        content: bytes,
        mime_type: str,
        checksum_sha256: str,
    ) -> ObjectWrite:
        try:
            result = self._client.put_object(
                bucket_name,
                object_key,
                BytesIO(content),
                len(content),
                content_type=mime_type,
                metadata={"sha256": checksum_sha256},
            )
        except (MinioException, HTTPError, OSError) as error:
            raise ObjectStorageError("MinIO object write failed") from error
        return ObjectWrite(
            bucket_name=bucket_name,
            object_key=object_key,
            object_version=result.version_id,
            etag=result.etag,
        )

    def get(
        self,
        *,
        bucket_name: str,
        object_key: str,
        object_version: str | None,
    ) -> bytes:
        try:
            response = self._client.get_object(
                bucket_name,
                object_key,
                version_id=object_version,
            )
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except (MinioException, HTTPError, OSError) as error:
            raise ObjectStorageError("MinIO object retrieval failed") from error
