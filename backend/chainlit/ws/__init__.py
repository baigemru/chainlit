"""The websocket transport.

``registry.py``    an in-memory registry of live sessions plus the policy
                   that reads it. It imports nothing from the rest of
                   ``chainlit`` and nothing from the transport, so it can be
                   tested and swapped on its own -- which matters because it
                   is the one thing here that would have to become shared
                   storage if this ever ran on more than one process.
``session.py``     one conversation, independent of the socket carrying it:
                   the pending question, the running task, the transcript,
                   the file spool, and the application's state dict.
``outbound.py``    the send side of one connection: a bounded queue with a
                   single writer task, which owns frame ordering, the
                   overflow policy and the close sequence.
``handshake.py``   what ``hello`` means: the claim on a session id, the
                   sweep of the sessions it supersedes, and the replay that
                   rebuilds the client's screen.
``connection.py``  the ``@websocket`` route: the reader and the heartbeat
                   sharing one socket.

Nothing is re-exported here on purpose: every consumer imports the module it
needs, and no process-wide instance is created -- who owns the registry is
a lifecycle question that belongs to whatever starts and stops the server.
"""
