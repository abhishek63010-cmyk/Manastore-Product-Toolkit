from typing import Any, Optional
from src.uploaders.base import BaseBlobUploader, UploadResult, UploadStatus


class FakeBlobUploader(BaseBlobUploader):
    """
    In-memory blob uploader for testing, offline simulation, and verification.
    
    Features:
    - Stores uploaded binaries in an internal dictionary.
    - Generates deterministic mock URLs: 'https://fake-blob.store.local/{key}'.
    - Full idempotency: re-uploading identical content at same key reuses existing URL.
    - Content-aware collision detection: uploading different content at same key fails with COLLISION.
    - Configurable failure simulation flags.
    """

    uploader_type: str = "fake_blob_uploader"

    def __init__(
        self,
        base_url: str = "https://fake-blob.store.local",
        simulate_failure: bool = False,
        simulate_missing_credentials: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.simulate_failure = simulate_failure
        self.simulate_missing_credentials = simulate_missing_credentials
        self.storage: dict[str, dict[str, Any]] = {}

    def upload_bytes(
        self,
        data: bytes,
        destination_key: str,
        content_type: Optional[str] = None,
        source_identity: str = "",
    ) -> UploadResult:
        if self.simulate_missing_credentials:
            return UploadResult(
                status=UploadStatus.FAILED,
                destination_key=destination_key,
                source_identity=source_identity,
                error_message="Missing blob storage credentials (simulation)",
            )

        if self.simulate_failure:
            return UploadResult(
                status=UploadStatus.FAILED,
                destination_key=destination_key,
                source_identity=source_identity,
                error_message="Simulated upload network/service failure",
            )

        c_hash = self.compute_content_hash(data)
        c_type = content_type or self.determine_content_type(destination_key)

        # Check existing key in storage for idempotency or collision
        if destination_key in self.storage:
            existing = self.storage[destination_key]
            if existing["content_hash"] == c_hash:
                # Idempotent re-upload: return existing URL
                return UploadResult(
                    status=UploadStatus.UPLOADED,
                    destination_key=destination_key,
                    blob_url=existing["blob_url"],
                    source_identity=source_identity,
                    content_type=c_type,
                    file_size_bytes=len(data),
                    content_hash=c_hash,
                    is_idempotent_reuse=True,
                )
            else:
                # Collision: different payload at the exact same destination key
                return UploadResult(
                    status=UploadStatus.COLLISION,
                    destination_key=destination_key,
                    source_identity=source_identity,
                    content_type=c_type,
                    file_size_bytes=len(data),
                    content_hash=c_hash,
                    error_message=(
                        f"Destination key '{destination_key}' already contains different content "
                        f"(existing hash: {existing['content_hash'][:8]}..., new hash: {c_hash[:8]}...)"
                    ),
                )

        # Store new asset
        blob_url = f"{self.base_url}/{destination_key}"
        self.storage[destination_key] = {
            "data": data,
            "blob_url": blob_url,
            "content_hash": c_hash,
            "content_type": c_type,
            "source_identity": source_identity,
        }

        return UploadResult(
            status=UploadStatus.UPLOADED,
            destination_key=destination_key,
            blob_url=blob_url,
            source_identity=source_identity,
            content_type=c_type,
            file_size_bytes=len(data),
            content_hash=c_hash,
            is_idempotent_reuse=False,
        )

    def get_uploaded_keys(self) -> list[str]:
        """Return list of all uploaded destination keys."""
        return list(self.storage.keys())

    def get_payload(self, key: str) -> Optional[bytes]:
        """Retrieve binary content stored at key."""
        entry = self.storage.get(key)
        return entry["data"] if entry else None

    def clear(self) -> None:
        """Reset internal storage."""
        self.storage.clear()
