"""What a stored blob is, and how a browser should be told to treat it.

Two callers, on opposite sides of the store, have to agree on this: the
upload path (``chainlit.persist``) writes the answer onto the object as
metadata, and the element file route
(``chainlit.controllers.project``) writes it into the response header. They
cannot import each other -- one is the runtime, the other the HTTP half --
and two copies of the rule is how a spreadsheet ends up opening as text in a
tab on one path and downloading on the other.

Nothing here touches a storage client, which is why it is not in
``base.py``: it is a policy about elements that both sides of the upload
apply, not part of the contract a backend implements.
"""

import mimetypes
from urllib.parse import quote

from chainlit.persistence.records import ElementRecord

__all__ = ["content_disposition", "element_mime"]


def element_mime(record: ElementRecord) -> str:
    """What the stored blob is, as far as anyone can tell.

    ``mime`` is NULL on every row written before the column was filled in,
    and a browser handed ``application/octet-stream`` for a PNG downloads it
    instead of drawing it -- so the name is asked second, and the fallback is
    only reached when the name has no extension either.
    """
    mime = record.mime
    if isinstance(mime, str) and mime:
        return mime
    guessed, _ = mimetypes.guess_type(record.name)
    return guessed or "application/octet-stream"


def content_disposition(name: str, mime: str) -> str:
    """How the browser should treat the blob, and what to call it.

    ``inline`` for what a browser renders itself and the UI embeds in the
    conversation; everything else is a download, and a download that opens a
    spreadsheet as text in a tab is a bug report.

    The filename encoding is Litestar's own (``response/file.py:192-197``),
    repeated rather than borrowed because ``File`` only serves a path and
    this serves bytes: a name that survives ``quote`` unchanged goes in the
    plain ``filename=``, anything else -- which is every Cyrillic name this
    fork's consumer produces -- in RFC 5987's ``filename*``.
    """
    disposition = (
        "inline"
        if mime.startswith("image/") or mime == "application/pdf"
        else "attachment"
    )
    quoted = quote(name)
    if quoted == name:
        return f'{disposition}; filename="{name}"'
    return f"{disposition}; filename*=utf-8''{quoted}"
