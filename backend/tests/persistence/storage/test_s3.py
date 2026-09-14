import os

import boto3  # type: ignore
import pytest
from moto import mock_aws

from chainlit.persistence.storage.s3 import S3StorageClient


# Fixtures for the mocked S3 bucket
@pytest.fixture
def aws_credentials():
    """Mocked AWS Credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture
def s3_mock(aws_credentials):
    """Moto mock S3 setup."""
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        # Create a mock bucket
        s3.create_bucket(Bucket="my-test-bucket")
        yield s3


@pytest.mark.asyncio
async def test_upload_file(s3_mock):
    # Initialize the S3StorageClient with the mock bucket
    client = S3StorageClient(bucket="my-test-bucket")

    # Call the upload_file method and await the result
    result = await client.upload_file(
        object_key="test.txt", data="This is a test file", mime="text/plain"
    )

    # The url is built from the endpoint boto3 was configured with, path
    # style. It used to be an AWS hostname with DEV_AWS_ENDPOINT spliced into
    # it, which on any other S3 -- Yandex Object Storage, MinIO -- named a
    # bucket on a host the deployment has never talked to.
    assert result["object_key"] == "test.txt"
    assert result["url"] == "https://s3.amazonaws.com/my-test-bucket/test.txt"

    # Verify that the file exists in the mock S3
    response = s3_mock.get_object(Bucket="my-test-bucket", Key="test.txt")
    assert response["Body"].read().decode() == "This is a test file"


@pytest.mark.asyncio
async def test_read_file(s3_mock):
    """The bytes back out, for the route that serves elements itself."""
    s3_mock.put_object(
        Bucket="my-test-bucket", Key="report.bin", Body=b"\x89PNG some bytes"
    )
    client = S3StorageClient(bucket="my-test-bucket")

    assert await client.read_file("report.bin") == b"\x89PNG some bytes"


@pytest.mark.asyncio
async def test_read_file_that_is_not_there(s3_mock):
    """``None``, not an exception: the caller turns this into a 404."""
    client = S3StorageClient(bucket="my-test-bucket")

    assert await client.read_file("never-uploaded.bin") is None
