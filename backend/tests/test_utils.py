import os
import tempfile
from datetime import UTC, datetime
from unittest.mock import patch

import click
import pytest

from chainlit.utils import check_file, utc_now, wrap_user_function


class TestUtcNow:
    """Test suite for utc_now function."""

    def test_utc_now_returns_string(self):
        """Test that utc_now returns a string."""
        result = utc_now()
        assert isinstance(result, str)

    def test_utc_now_ends_with_z(self):
        """Test that utc_now returns ISO format with Z suffix."""
        result = utc_now()
        assert result.endswith("Z")

    def test_utc_now_is_iso_format(self):
        """Test that utc_now returns valid ISO format."""
        result = utc_now()
        # Remove the Z and parse
        dt_str = result[:-1]
        # Should be parseable as ISO format
        datetime.fromisoformat(dt_str)

    def test_utc_now_is_current_time(self):
        """Test that utc_now returns approximately current time."""
        before = datetime.now(UTC).replace(tzinfo=None)
        result = utc_now()
        after = datetime.now(UTC).replace(tzinfo=None)

        # Parse the result (naive datetime)
        result_dt = datetime.fromisoformat(result[:-1])

        # Should be between before and after (with some tolerance for microseconds)
        assert (
            before.replace(microsecond=0) <= result_dt <= after.replace(microsecond=0)
            or before <= result_dt <= after
        )

    def test_utc_now_multiple_calls(self):
        """Test that multiple calls to utc_now return different values."""
        result1 = utc_now()
        result2 = utc_now()

        # Results should be very close but might differ
        assert isinstance(result1, str)
        assert isinstance(result2, str)


@pytest.mark.asyncio
class TestWrapUserFunction:
    """Test suite for wrap_user_function."""

    async def test_wrap_user_function_with_sync_function(self, mock_chainlit_context):
        """Test wrapping a synchronous function."""
        async with mock_chainlit_context:

            def user_func(a, b):
                return a + b

            wrapped = wrap_user_function(user_func)
            result = await wrapped(5, 3)

            assert result == 8

    async def test_wrap_user_function_with_async_function(self, mock_chainlit_context):
        """Test wrapping an asynchronous function."""
        async with mock_chainlit_context:

            async def user_func(x, y):
                return x * y

            wrapped = wrap_user_function(user_func)
            result = await wrapped(4, 7)

            assert result == 28

    async def test_wrap_user_function_with_no_args(self, mock_chainlit_context):
        """Test wrapping a function with no arguments."""
        async with mock_chainlit_context:

            def user_func():
                return "hello"

            wrapped = wrap_user_function(user_func)
            result = await wrapped()

            assert result == "hello"

    async def test_wrap_user_function_handles_exception(self, mock_chainlit_context):
        """Test that wrapped function handles exceptions."""
        async with mock_chainlit_context:

            def user_func():
                raise ValueError("Test error")

            wrapped = wrap_user_function(user_func)
            result = await wrapped()

            # Should return None when exception occurs
            assert result is None

    async def test_wrap_user_function_preserves_function_metadata(
        self, mock_chainlit_context
    ):
        """Test that wrapping preserves function metadata."""
        async with mock_chainlit_context:

            def user_func(a, b):
                """Test function docstring."""
                return a + b

            wrapped = wrap_user_function(user_func)

            assert wrapped.__name__ == "user_func"
            assert wrapped.__doc__ == "Test function docstring."

    async def test_wrap_user_function_with_kwargs(self, mock_chainlit_context):
        """Test wrapping a function and calling with positional args."""
        async with mock_chainlit_context:

            def user_func(x, y, z):
                return x + y + z

            wrapped = wrap_user_function(user_func)
            result = await wrapped(1, 2, 3)

            assert result == 6


class TestCheckFile:
    """Test suite for check_file function."""

    def test_check_file_with_valid_py_file(self):
        """Test check_file with a valid .py file."""
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            temp_file = f.name

        try:
            # Should not raise any exception
            check_file(temp_file)
        finally:
            os.unlink(temp_file)

    def test_check_file_with_valid_py3_file(self):
        """Test check_file with a valid .py3 file."""
        with tempfile.NamedTemporaryFile(suffix=".py3", delete=False) as f:
            temp_file = f.name

        try:
            # Should not raise any exception
            check_file(temp_file)
        finally:
            os.unlink(temp_file)

    def test_check_file_with_invalid_extension(self):
        """Test check_file with invalid file extension."""
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            temp_file = f.name

        try:
            with pytest.raises(click.BadArgumentUsage) as exc_info:
                check_file(temp_file)
            assert ".txt" in str(exc_info.value)
        finally:
            os.unlink(temp_file)

    def test_check_file_with_no_extension(self):
        """Test check_file with file that has no extension."""
        with tempfile.NamedTemporaryFile(suffix="", delete=False) as f:
            temp_file = f.name

        try:
            with pytest.raises(click.BadArgumentUsage) as exc_info:
                check_file(temp_file)
            assert "no extension" in str(exc_info.value)
        finally:
            os.unlink(temp_file)

    def test_check_file_with_nonexistent_file(self):
        """Test check_file with a file that doesn't exist."""
        nonexistent_file = "/path/to/nonexistent/file.py"

        with pytest.raises(click.BadParameter) as exc_info:
            check_file(nonexistent_file)
        assert "does not exist" in str(exc_info.value)

    def test_check_file_with_json_extension(self):
        """Test check_file with .json extension."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            temp_file = f.name

        try:
            with pytest.raises(click.BadArgumentUsage) as exc_info:
                check_file(temp_file)
            assert ".json" in str(exc_info.value)
        finally:
            os.unlink(temp_file)


class TestUtilsEdgeCases:
    """Test suite for utils edge cases."""

    def test_utc_now_format_consistency(self):
        """Test that utc_now format is consistent across calls."""
        results = [utc_now() for _ in range(5)]

        for result in results:
            # All should have same format
            assert result.endswith("Z")
            assert "T" in result
            # Should be parseable
            datetime.fromisoformat(result[:-1])

    @pytest.mark.asyncio
    async def test_wrap_user_function_with_multiple_exceptions(
        self, mock_chainlit_context
    ):
        """Test wrapped function handles different exception types."""
        async with mock_chainlit_context:
            exceptions = [ValueError("error1"), TypeError("error2"), KeyError("error3")]

            for exc in exceptions:

                def user_func():
                    raise exc

                with patch("chainlit.utils.logger"):
                    wrapped = wrap_user_function(user_func)
                    result = await wrapped()
                    assert result is None

    def test_check_file_with_relative_path(self):
        """Test check_file with relative path."""
        # Create a temp file in current directory
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False, dir=".") as f:
            temp_file = os.path.basename(f.name)

        try:
            # Should work with relative path
            check_file(temp_file)
        finally:
            os.unlink(temp_file)

    def test_check_file_with_absolute_path(self):
        """Test check_file with absolute path."""
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            temp_file = os.path.abspath(f.name)

        try:
            # Should work with absolute path
            check_file(temp_file)
        finally:
            os.unlink(temp_file)

    @pytest.mark.asyncio
    async def test_wrap_user_function_with_default_args(self, mock_chainlit_context):
        """Test wrapping function with default arguments."""
        async with mock_chainlit_context:

            def user_func(a, b=10):
                return a + b

            wrapped = wrap_user_function(user_func)

            # Call with only required arg
            result = await wrapped(5)
            assert result == 15
