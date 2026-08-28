"""The socket protocol spec: incoming frames in, outgoing frames out.

`test_socket.py` states the protocol imperatively, in terms of
``chainlit.socket``'s function names and the shape of the mocks it patches
around them. That is 3224 lines of assertions about a transport we are
deleting -- rewriting it means rewriting them, which means the rewrite has no
baseline at all.

This package states the same behaviour as *data*: a table of scenarios, each
one "given this session state, these frames arrive, these frames go out, in
this order". A driver turns a scenario into calls against one implementation.
The socket.io driver (``legacy``) left with the transport it drove; the
driver for the native websocket runs the same table.

The frames in the table use the **new** protocol tags from
``chainlit.protocol``, not socket.io event names. The table therefore
describes what we are building, checked today against what exists -- which is
also how the collapsed pairs (``ask_timeout``+``clear_ask`` -> ``ask.end``)
get their first real test.

Everything here is free of ``chainlit`` imports. That boundary is the whole
deliverable: it is what lets the table survive the transport it was written
against.
"""
