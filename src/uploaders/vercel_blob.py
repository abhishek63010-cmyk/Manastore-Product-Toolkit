import os
from typing import Optional
from dotenv import load_dotenv
import requests

from src.uploaders.base import BaseBlobUploader, UploadResult, UploadStatus

# Load local .env if available
load_dotenv()


class VercelBlobUploader(BaseBlobUploader):
    """
    Uploader for Vercel Blob storage using the Vercel Blob HTTP REST API.
    
    Security & Configuration:
    - Reads token from `BLOB_READ_WRITE_TOKEN` environment variable or constructor.
    - Never prints or exposes the token in exceptions or logs.
    - Does not connect or validate credentials at module import time.
    - Sets `x-add-random-suffix: 0` to maintain deterministic destination URLs.
    """

    uploader_type: str = "vercel_blob"
    API_BASE_URL: str = "https://blob.vercel-storage.com"

    def __init__(
        self,
        token: Optional[str] = None,
        api_base_url: Optional[str] = None,
        timeout_seconds: int = 30,
    ):
        self._token = token or os.getenv("BLOB_READ_WRITE_TOKEN")
        self.api_base_url = (api_base_url or self.API_BASE_URL).rstrip("/")
        self.timeout_seconds = timeout_seconds

    @property
    def has_credentials(self) -> bool:
        """Check if a token is configured without exposing it."""
        return bool(self._token and len(self._token.strip()) > 0)

    def upload_bytes(
        self,
        data: bytes,
        destination_key: str,
        content_type: Optional[str] = None,
        source_identity: str = "",
    ) -> UploadResult:
        """Upload raw binary data to Vercel Blob Storage."""
        if not self.has_credentials:
            return UploadResult(
                status=UploadStatus.FAILED,
                destination_key=destination_key,
                source_identity=source_identity,
                error_message="Vercel Blob token not configured (set BLOB_READ_WRITE_TOKEN)",
            )

        c_type = content_type or self.determine_content_type(destination_key)
        c_hash = self.compute_content_hash(data)

        # Build Vercel Blob REST request
        clean_path = destination_key.lstrip("/")
        url = f"{self.api_base_url}/{clean_path}"

        headers = {
            "Authorization": f"Bearer {self._token}",
            "x-api-version": "7",
            "x-add-random-suffix": "0",  # Maintain deterministic destination key
            "Content-Type": c_type,
        }

        try:
            response = requests.put(
                url,
                data=data,
                headers=headers,
                timeout=self.timeout_seconds,
            )

            if response.status_code in (200, 201):
                res_data = response.json()
                blob_url = res_data.get("url")
                return UploadResult(
                    status=UploadStatus.UPLOADED,
                    destination_key=destination_key,
                    blob_url=blob_url,
                    source_identity=source_identity,
                    content_type=c_type,
                    file_size_bytes=len(data),
                    content_hash=c_hash,
                )
            else:
                # Sanitize error message to prevent accidental token leak
                sanitized_status = response.status_code
                return UploadResult(
                    status=UploadStatus.FAILED,
                    destination_key=destination_key,
                    source_identity=source_identity,
                    content_type=c_type,
                    file_size_bytes=len(data),
                    content_hash=c_hash,
                    error_message=f"Vercel Blob HTTP Error {sanitized_status}",
                )

        except requests.exceptions.RequestException as e:
            # Safely report error without token
            return UploadResult(
                status=UploadStatus.FAILED,
                destination_key=destination_key,
                source_identity=source_identity,
                content_type=c_type,
                file_size_bytes=len(data),
                content_hash=c_hash,
                error_message=f"Network/transport exception during upload: {type(e).__name__}",
            )
