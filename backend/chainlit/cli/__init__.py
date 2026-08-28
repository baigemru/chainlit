"""The ``chainlit`` command.

``chainlit run app.py`` loads the user's module, builds a Litestar application
with :class:`chainlit.plugin.ChainlitPlugin` as its only plugin, and hands it
to uvicorn. Everything the plugin needs -- the callbacks the module registered,
the data layer, the transit store -- is resolved here, once, and nothing about
the running app is module state: a second ``build_app`` call is a second app.

Persistence is opt-in through the environment: set ``DATABASE_URL`` to an
async SQLAlchemy URL (``postgresql+asyncpg://...``, ``sqlite+aiosqlite://...``)
and the app runs with a data layer; leave it unset and it runs without one,
which is the default Chainlit has always had.

The asyncio task class is left untouched on purpose. ``nest_asyncio.apply()``
rebinds ``asyncio.Task`` to the pure Python implementation while
``asyncio.current_task`` stays bound to the C accelerator; the two then
disagree and anyio raises inside every request. Nothing here needs a
re-entrant loop -- ``asyncio.run(start())`` is a top-level call -- and
``tests/test_cli.py`` asserts that nothing imported here changes that.
"""

import asyncio
import os
import webbrowser
from contextlib import asynccontextmanager, suppress
from typing import AsyncIterator, Callable, Optional

import click
import uvicorn
from litestar import Litestar

from chainlit.config import (
    BACKEND_ROOT,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_ROOT_PATH,
    config,
    init_config,
    lint_translations,
    load_module,
    reload_config,
)
from chainlit.logger import logger
from chainlit.plugin import ChainlitPlugin
from chainlit.secret import random_secret
from chainlit.utils import check_file

#: The environment variable that turns persistence on. Documented on ``run``.
DATABASE_URL_ENV = "DATABASE_URL"

#: What the Cypress harness (``cypress/support/run.ts``) waits for on stdout.
#: ``tests/test_cli_contract.py`` pins it; reword both or neither.
ANNOUNCE = "Your app is available at"


# Create the main command group for Chainlit CLI
@click.group(context_settings={"auto_envvar_prefix": "CHAINLIT"})
@click.version_option(prog_name="Chainlit")
def cli():
    return


def _persistence_from_env():
    """The data layer named by ``DATABASE_URL``, or ``None`` for no data layer.

    Imported lazily: ``chainlit.persistence`` pulls in SQLAlchemy and
    advanced_alchemy, which an app with no database never needs to load.
    """
    url = os.environ.get(DATABASE_URL_ENV)
    if not url:
        return None
    from chainlit.persistence import Persistence

    return Persistence.from_url(url)


def _app_url() -> str:
    host = config.run.host
    port = config.run.port
    root_path = os.environ.get("CHAINLIT_ROOT_PATH", "")
    scheme = "https" if config.run.ssl_cert else "http"
    shown_host = "localhost" if host == DEFAULT_HOST else host
    return f"{scheme}://{shown_host}:{port}{root_path}"


async def _watch_files_for_changes(
    stop_event: asyncio.Event, on_reload: Callable[[], None]
) -> None:
    """Reload the config and the user's module when a source file changes.

    ``on_reload`` is the plugin's client broadcast: every open tab is told
    to drop its session and reload, the way the old socket.io server did.
    """
    from watchfiles import awatch

    extensions = (".py",)
    files = ("chainlit.md", "config.toml")
    async for changes in awatch(config.root, stop_event=stop_event):
        for change_type, file_path in changes:
            file_name = os.path.basename(file_path)
            file_ext = os.path.splitext(file_name)[1]
            if file_ext.lower() not in extensions and file_name.lower() not in files:
                continue
            logger.info(f"File {change_type.name}: {file_name}. Reloading app...")
            try:
                reload_config()
            except Exception as e:
                logger.error(f"Error reloading config: {e}")
                return
            if config.run.module_name:
                try:
                    load_module(config.run.module_name, force_refresh=True)
                except Exception as e:
                    logger.error(f"Error reloading module: {e}")
            on_reload()
            break


@asynccontextmanager
async def _cli_lifespan(app: Litestar) -> AsyncIterator[None]:
    """What the CLI, and only the CLI, adds to the app's lifecycle.

    Announcing the URL and opening a browser are things a terminal user
    wants and an embedding host does not, so they live here rather than in
    the plugin. This runs before the plugin's own lifespan (Litestar keeps
    the order the app was given), so the announce means "starting", not
    "ready" -- the contract test polls for the bind separately.
    """
    url = _app_url()
    # ``click.echo`` rather than the logger: root logging is configured by the
    # plugin's lifespan, which has not run yet at this point.
    click.echo(f"{ANNOUNCE} {url}")

    browser_task: Optional[asyncio.Task[None]] = None
    if not config.run.headless:

        async def open_browser() -> None:
            # A delay so the socket is bound by the time the tab loads.
            await asyncio.sleep(1)
            webbrowser.open(url)

        browser_task = asyncio.create_task(open_browser())

    stop_event = asyncio.Event()
    watch_task: Optional[asyncio.Task[None]] = None
    if config.run.watch:
        plugin = app.plugins.get(ChainlitPlugin)
        watch_task = asyncio.create_task(
            _watch_files_for_changes(stop_event, plugin.runner.reload_clients)
        )

    try:
        yield
    finally:
        stop_event.set()
        for task in (browser_task, watch_task):
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


def build_app(target: str) -> Litestar:
    """Load ``target`` and build the Litestar app that serves it."""
    check_file(target)
    config.run.module_name = target
    load_module(config.run.module_name)

    return Litestar(
        plugins=[
            ChainlitPlugin(
                config,
                persistence=_persistence_from_env(),
                configure_logging=True,
            )
        ],
        lifespan=[_cli_lifespan],
    )


# Define the function to run Chainlit with provided options
def run_chainlit(target: str):
    host = os.environ.get("CHAINLIT_HOST", DEFAULT_HOST)
    port = int(os.environ.get("CHAINLIT_PORT", DEFAULT_PORT))
    # The prefix the app is served under. It is handed to uvicorn as
    # ``root_path`` rather than to ``Litestar(path=...)``: uvicorn puts it in
    # ``scope["root_path"]`` and prepends it to ``scope["path"]`` (ASGI 3),
    # and Litestar's router strips exactly that prefix before matching, so
    # every route, static file and the ``/ws`` handshake see the same paths
    # they would at the root. ``Litestar(path=...)`` would instead register
    # every route *with* the prefix and leave ``root_path`` empty, which
    # breaks any URL the app builds from ``request.base_url``. The cost is
    # the standard one: the proxy in front must forward the request with the
    # prefix intact, as a reverse proxy configured for a root path does.
    root_path = os.environ.get("CHAINLIT_ROOT_PATH", DEFAULT_ROOT_PATH)

    ssl_certfile = os.environ.get("CHAINLIT_SSL_CERT", None)
    ssl_keyfile = os.environ.get("CHAINLIT_SSL_KEY", None)

    ws_per_message_deflate_env = os.environ.get(
        "UVICORN_WS_PER_MESSAGE_DEFLATE", "true"
    )
    ws_per_message_deflate = ws_per_message_deflate_env.lower() in [
        "true",
        "1",
        "yes",
    ]  # Convert to boolean

    ws_protocol = os.environ.get("UVICORN_WS_PROTOCOL", "auto")

    config.run.host = host
    config.run.port = port
    config.run.root_path = root_path

    app = build_app(target)

    log_level = "debug" if config.run.debug else "error"

    # Start the server
    async def start():
        server_config = uvicorn.Config(
            app,
            host=host,
            port=port,
            root_path=root_path,
            ws=ws_protocol,
            log_level=log_level,
            ws_per_message_deflate=ws_per_message_deflate,
            ssl_keyfile=ssl_keyfile,
            ssl_certfile=ssl_certfile,
        )
        server = uvicorn.Server(server_config)
        await server.serve()

    # The plain asyncio loop, not uvloop: nothing here needs the speed, and
    # the user's callbacks may rely on asyncio-only behaviour.
    asyncio.run(start())


# Define the "run" command for Chainlit CLI
@cli.command("run")
@click.argument("target", required=True, envvar="RUN_TARGET")
@click.option(
    "-w",
    "--watch",
    default=False,
    is_flag=True,
    envvar="WATCH",
    help="Reload the app when the module changes",
)
@click.option(
    "-h",
    "--headless",
    default=False,
    is_flag=True,
    envvar="HEADLESS",
    help="Will prevent to auto open the app in the browser",
)
@click.option(
    "-d",
    "--debug",
    default=False,
    is_flag=True,
    envvar="DEBUG",
    help="Set the log level to debug",
)
@click.option(
    "-c",
    "--ci",
    default=False,
    is_flag=True,
    envvar="CI",
    help="Flag to run in CI mode",
)
@click.option(
    "--ssl-cert",
    default=None,
    envvar="CHAINLIT_SSL_CERT",
    help="Specify the file path for the SSL certificate.",
)
@click.option(
    "--ssl-key",
    default=None,
    envvar="CHAINLIT_SSL_KEY",
    help="Specify the file path for the SSL key",
)
@click.option("--host", help="Specify a different host to run the server on")
@click.option("--port", help="Specify a different port to run the server on")
@click.option("--root-path", help="Specify a different root path to run the server on")
def chainlit_run(
    target,
    watch,
    headless,
    debug,
    ci,
    ssl_cert,
    ssl_key,
    host,
    port,
    root_path,
):
    """Run the Chainlit app in TARGET.

    Persistence is enabled by the DATABASE_URL environment variable: an
    async SQLAlchemy URL such as postgresql+asyncpg://user:pw@host/db. When
    it is unset the app runs without a data layer.
    """
    if host:
        os.environ["CHAINLIT_HOST"] = host
    if port:
        os.environ["CHAINLIT_PORT"] = port
    if bool(ssl_cert) != bool(ssl_key):
        raise click.UsageError(
            "Both --ssl-cert and --ssl-key must be provided together."
        )
    if ssl_cert:
        os.environ["CHAINLIT_SSL_CERT"] = ssl_cert
        os.environ["CHAINLIT_SSL_KEY"] = ssl_key
    if root_path:
        os.environ["CHAINLIT_ROOT_PATH"] = root_path
    if ci:
        logger.info("Running in CI mode")
        # This is required to have OpenAI LLM providers available for the CI run
        os.environ["OPENAI_API_KEY"] = "sk-FAKE-OPENAI-API-KEY"

    config.run.headless = headless
    config.run.debug = debug
    config.run.ci = ci
    config.run.watch = watch
    config.run.ssl_cert = ssl_cert
    config.run.ssl_key = ssl_key

    run_chainlit(target)


@cli.command("hello")
@click.argument("args", nargs=-1)
def chainlit_hello(args=None, **kwargs):
    hello_path = os.path.join(BACKEND_ROOT, "sample", "hello.py")
    run_chainlit(hello_path)


@cli.command("init")
@click.argument("args", nargs=-1)
def chainlit_init(args=None, **kwargs):
    init_config(log=True)


@cli.command("create-secret")
@click.argument("args", nargs=-1)
def chainlit_create_secret(args=None, **kwargs):
    print(
        f'Copy the following secret into your .env file. Once it is set, changing it will logout all users with active sessions.\nCHAINLIT_AUTH_SECRET="{random_secret()}"'
    )


@cli.command("lint-translations")
@click.argument("args", nargs=-1)
def chainlit_lint_translations(args=None, **kwargs):
    lint_translations()
