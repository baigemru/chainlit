import os
from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, Optional, Union

from chainlit.logger import logger

storage_expiry_time = int(os.getenv("STORAGE_EXPIRY_TIME", 3600))


class BaseStorageClient(ABC):
    """Base class for non-text data persistence like Azure Data Lake, S3, Google Storage, etc."""

    @abstractmethod
    async def upload_file(
        self,
        object_key: str,
        data: Union[bytes, str],
        mime: str = "application/octet-stream",
        overwrite: bool = True,
        content_disposition: str | None = None,
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def delete_file(self, object_key: str) -> bool:
        pass

    @abstractmethod
    async def get_read_url(self, object_key: str) -> str:
        """A presigned url for the object, valid for ``storage_expiry_time``.

        No caller inside the package (GCS uses its own at upload). Kept as
        the escape hatch for the redirect delivery mode: a deployment that
        configures CORS on its bucket could 302 here instead of proxying.
        """

    @abstractmethod
    async def read_file(self, object_key: str) -> Optional[bytes]:
        """The object's bytes, or ``None`` when it cannot be read.

        The application serves element blobs itself rather than handing the
        browser a url into the bucket: a private bucket makes every stored
        url dead, and a redirect to a presigned one turns the ``fetch`` the
        text, dataframe and pdf components do into a credentialed
        cross-origin request the consumer's bucket is not configured to
        answer.

        Whole object, not a stream — because the workload is report files
        (images, pdf, xlsx: kilobytes to a few megabytes), and buffering
        them keeps the route trivially correct. Not because streaming is
        impossible: ``get_object`` raises ``NoSuchKey`` at the call, before
        any body is read, so a 404 stays possible, and ``iter_chunks``
        takes its chunk size as a parameter. The day someone persists a
        video, the upgrade is a chunked stream with ``Accept-Ranges`` —
        this signature is the thing to change.

        ``None`` rather than an exception, for the same reason
        :meth:`delete_file` returns ``False``: the caller decides what a
        missing blob means, and here it means the same ``404`` as a missing
        element.
        """

    @abstractmethod
    async def close(self) -> None:
        pass


async def discard_blobs(
    storage: Optional[BaseStorageClient], object_keys: Iterable[str]
) -> None:
    """Drop the blobs of rows that are gone. Never raises, never blames.

    The rows are already deleted when this runs -- deliberately, because the
    reverse order leaves a row pointing at bytes that are not there. So a
    store that refuses is logged and left: failing the operation would
    neither bring the row back nor delete the object, and on the writer's
    path it would send an innocent batch into the per-op replay.

    The key is in the message because the clients' own warnings do not carry
    it, and an orphan is only findable by name. ``None`` -- an application
    with nowhere to put blobs -- has nothing to discard and says nothing.
    """
    if storage is None:
        return
    for key in object_keys:
        try:
            deleted = await storage.delete_file(key)
        except Exception:
            logger.warning("Could not delete the stored blob %s", key, exc_info=True)
            continue
        if not deleted:
            # The clients answer False rather than raise (see delete_file);
            # the object either was not there or the bucket said no.
            logger.warning("The store did not delete the blob %s", key)
