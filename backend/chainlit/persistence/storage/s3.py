import contextlib
from typing import Any, Dict, Optional, Union

import boto3  # type: ignore
from litestar.concurrency import sync_to_thread

from chainlit.logger import logger
from chainlit.persistence.storage.base import BaseStorageClient, storage_expiry_time


class S3StorageClient(BaseStorageClient):
    """
    Class to enable Amazon S3 storage provider
    """

    def __init__(self, bucket: str, **kwargs: Any):
        try:
            self.bucket = bucket
            self.client = boto3.client("s3", **kwargs)
            logger.info("S3StorageClient initialized")
        except Exception as e:
            logger.warning(f"S3StorageClient initialization error: {e}")

    def sync_get_read_url(self, object_key: str) -> str:
        try:
            url = self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": object_key},
                ExpiresIn=storage_expiry_time,
            )
            return url
        except Exception as e:
            logger.warning(f"S3StorageClient, get_read_url error: {e}")
            return object_key

    async def get_read_url(self, object_key: str) -> str:
        return await sync_to_thread(self.sync_get_read_url, object_key)

    def sync_upload_file(
        self,
        object_key: str,
        data: Union[bytes, str],
        mime: str = "application/octet-stream",
        overwrite: bool = True,
        content_disposition: str | None = None,
    ) -> Dict[str, Any]:
        try:
            if content_disposition is not None:
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=object_key,
                    Body=data,
                    ContentType=mime,
                    ContentDisposition=content_disposition,
                )
            else:
                self.client.put_object(
                    Bucket=self.bucket, Key=object_key, Body=data, ContentType=mime
                )
            # The endpoint boto3 was actually configured with, path-style.
            # The old form interpolated ``DEV_AWS_ENDPOINT`` into an AWS
            # hostname, so every deployment on another S3 -- Yandex Object
            # Storage, MinIO -- wrote a url pointing at a bucket nobody
            # owns. Nothing reads this column any more (elements are served
            # by ``/project/thread/{id}/element/{id}/file``); it is kept
            # because it is the only record of where a blob went.
            url = f"{self.client.meta.endpoint_url}/{self.bucket}/{object_key}"
            return {"object_key": object_key, "url": url}
        except Exception as e:
            logger.warning(f"S3StorageClient, upload_file error: {e}")
            return {}

    async def upload_file(
        self,
        object_key: str,
        data: Union[bytes, str],
        mime: str = "application/octet-stream",
        overwrite: bool = True,
        content_disposition: str | None = None,
    ) -> Dict[str, Any]:
        return await sync_to_thread(
            self.sync_upload_file,
            object_key,
            data,
            mime,
            overwrite,
            content_disposition,
        )

    def sync_read_file(self, object_key: str) -> Optional[bytes]:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=object_key)
            # The body holds the connection until it is drained or closed,
            # and a leaked one starves the urllib3 pool a request at a time.
            with contextlib.closing(response["Body"]) as body:
                data: bytes = body.read()
            return data
        except Exception as e:
            logger.warning(f"S3StorageClient, read_file error: {e}")
            return None

    async def read_file(self, object_key: str) -> Optional[bytes]:
        return await sync_to_thread(self.sync_read_file, object_key)

    def sync_delete_file(self, object_key: str) -> bool:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=object_key)
            return True
        except Exception as e:
            logger.warning(f"S3StorageClient, delete_file error: {e}")
            return False

    async def delete_file(self, object_key: str) -> bool:
        return await sync_to_thread(self.sync_delete_file, object_key)

    async def close(self) -> None:
        # boto3 is synchronous; its close() returns None, not an awaitable.
        self.client.close()
