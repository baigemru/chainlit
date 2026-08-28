import functools
import importlib
import inspect
import os
from asyncio import CancelledError
from datetime import UTC, datetime
from typing import Callable

import click
from packaging import version

from chainlit.logger import logger


def utc_now():
    dt = datetime.now(UTC).replace(tzinfo=None)
    return dt.isoformat() + "Z"


def timestamp_utc(timestamp: float):
    dt = datetime.fromtimestamp(timestamp, UTC).replace(tzinfo=None)
    return dt.isoformat() + "Z"


def wrap_user_function(user_function: Callable) -> Callable:
    """
    Wraps a user-defined function to accept arguments as a dictionary.

    The wrapper only maps positional arguments onto the function's parameter
    names and logs an exception instead of raising it. Task bookkeeping --
    which the old ``with_task`` flag did through the emitter -- belongs to
    ``chainlit.runner`` now, which runs every hook of the session itself.

    Args:
        user_function (Callable): The user-defined function to wrap.

    Returns:
        Callable: The wrapped function.
    """

    @functools.wraps(user_function)
    async def wrapper(*args):
        # Get the parameter names of the user-defined function
        user_function_params = list(inspect.signature(user_function).parameters.keys())

        # Create a dictionary of parameter names and their corresponding values from *args
        params_values = {
            param_name: arg for param_name, arg in zip(user_function_params, args)
        }

        try:
            # Call the user-defined function with the arguments
            if inspect.iscoroutinefunction(user_function):
                return await user_function(**params_values)
            else:
                return user_function(**params_values)
        except CancelledError:
            pass
        except Exception as e:
            logger.exception(e)

    return wrapper


def make_module_getattr(registry):
    """Leverage PEP 562 to make imports lazy in an __init__.py

    The registry must be a dictionary with the items to import as keys and the
    modules they belong to as a value.
    """

    def __getattr__(name):
        module_path = registry[name]
        module = importlib.import_module(module_path, __package__)
        return getattr(module, name)

    return __getattr__


def check_module_version(name, required_version):
    """
    Check the version of a module.

    Args:
        name (str): A module name.
        version (str): Minimum version.

    Returns:
        (bool): Return True if the module is installed and the version
            match the minimum required version.
    """
    try:
        module = importlib.import_module(name)
    except ModuleNotFoundError:
        return False
    return version.parse(module.__version__) >= version.parse(required_version)


def check_file(target: str):
    # Define accepted file extensions for Chainlit
    ACCEPTED_FILE_EXTENSIONS = ("py", "py3")

    _, extension = os.path.splitext(target)

    # Check file extension
    if extension[1:] not in ACCEPTED_FILE_EXTENSIONS:
        if extension[1:] == "":
            raise click.BadArgumentUsage(
                "Chainlit requires raw Python (.py) files, but the provided file has no extension."
            )
        else:
            raise click.BadArgumentUsage(
                f"Chainlit requires raw Python (.py) files, not {extension}."
            )

    if not os.path.exists(target):
        raise click.BadParameter(f"File does not exist: {target}")
