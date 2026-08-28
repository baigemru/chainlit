"""Object storage for element blobs: the ``BaseStorageClient`` protocol and
the two backends with a consumer -- S3 (what the deployed app uses) and GCS.

Lives under ``persistence`` because ``Persistence.storage`` is the only
place a client is ever plugged in; a package of its own next to the
database layer was the last remnant of the old ``chainlit.data`` tree.
"""
