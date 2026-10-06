import hashlib
import mimetypes
import re
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Union
from pydantic import BaseModel, Field


class UploadStatus(str, Enum):
    """Lifecycle upload status."""
    UPLOADED = "UPLOADED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    COLLISION = "COLLISION"


class UploadResult(BaseModel):
    """Structured record of a single blob upload operation."""
    status: UploadStatus
    destination_key: str = Field(
        ...,
        description="Deterministic path / key of the asset in blob storage"
    )
    blob_url: Optional[str] = Field(
        default=None,
        description="Public HTTP URL of the hosted blob once successfully uploaded"
    )
    source_identity: str = Field(
        default="",
        description="Local path, archive member, or source reference of the file"
    )
    content_type: Optional[str] = Field(
        default=None,
        description="MIME type of the uploaded asset (e.g., 'image/png', 'image/jpeg')"
    )
    file_size_bytes: int = Field(
        default=0,
        ge=0,
        description="Byte length of the payload"
    )
    content_hash: Optional[str] = Field(
        default=None,
        description="SHA-256 hash of the uploaded payload for idempotency verification"
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Sanitized error details if upload failed or collided"
    )
    is_idempotent_reuse: bool = Field(
        default=False,
        description="True if an existing identical asset at destination_key was reused"
    )


class BaseBlobUploader(ABC):
    """
    Abstract storage-agnostic blob uploader interface.
    
    Principles:
    - Decoupled from specific cloud providers (Vercel Blob, S3, local, mock).
    - Guarantees deterministic, collision-safe destination keys.
    - Strictly distinguishes source URLs from hosted blob URLs.
    - Preserves MIME content types and enforces idempotency.
    """

    uploader_type: str = "base_blob_uploader"

    @abstractmethod
    def upload_bytes(
        self,
        data: bytes,
        destination_key: str,
        content_type: Optional[str] = None,
        source_identity: str = "",
    ) -> UploadResult:
        """Upload raw bytes to the destination key."""
        pass

    def upload_file(
        self,
        file_path: Union[str, Path],
        destination_key: str,
        content_type: Optional[str] = None,
    ) -> UploadResult:
        """Read a local file and upload its content to destination key."""
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return UploadResult(
                status=UploadStatus.FAILED,
                destination_key=destination_key,
                source_identity=str(path),
                error_message=f"Local file does not exist: {path}",
            )

        data = path.read_bytes()
        c_type = content_type or self.determine_content_type(path.name)
        return self.upload_bytes(
            data=data,
            destination_key=destination_key,
            content_type=c_type,
            source_identity=str(path),
        )

    @staticmethod
    def compute_content_hash(data: bytes) -> str:
        """Compute SHA-256 hex digest of content for idempotency validation."""
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def determine_content_type(filename_or_ext: str) -> str:
        """Detect MIME type from filename or extension with image fallbacks."""
        ext = Path(filename_or_ext).suffix.lower()
        content_type, _ = mimetypes.guess_type(filename_or_ext)
        if content_type:
            return content_type

        fallbacks = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".avif": "image/avif",
        }
        return fallbacks.get(ext, "application/octet-stream")

    @classmethod
    def generate_destination_key(
        cls,
        supplier_name: str,
        catalog_name: str,
        variant_code: str,
        filename: str,
        prefix: Optional[str] = None,
    ) -> str:
        """
        Generate a deterministic, sanitized blob storage destination key.
        
        IMPORTANT:
        - Uses variant_code (unique variant identity), NEVER supplier_sku alone.
        - Same inputs produce the exact same destination key.
        - Different variants produce distinct destination keys.
        """
        clean_supplier = cls._sanitize_path_segment(supplier_name)
        clean_catalog = cls._sanitize_path_segment(catalog_name)
        clean_variant = cls._sanitize_path_segment(variant_code)
        clean_filename = cls._sanitize_filename(filename)

        parts = []
        if prefix:
            for sub_p in prefix.replace("\\", "/").split("/"):
                clean_p = cls._sanitize_path_segment(sub_p)
                if clean_p:
                    parts.append(clean_p)

        clean_supplier = cls._sanitize_path_segment(supplier_name)
        clean_catalog = cls._sanitize_path_segment(catalog_name)
        clean_variant = cls._sanitize_path_segment(variant_code)
        clean_filename = cls._sanitize_filename(filename)

        parts.extend([clean_supplier, clean_catalog, clean_variant, clean_filename])
        return "/".join(p for p in parts if p)

    @staticmethod
    def _sanitize_path_segment(segment: str) -> str:
        """Sanitize a folder/segment path component."""
        if not segment:
            return "unknown"
        cleaned = re.sub(r"[^\w\-]+", "-", segment.strip())
        return re.sub(r"-+", "-", cleaned).strip("-")

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """Sanitize file name while preserving valid extensions."""
        p = Path(filename)
        stem = p.stem.strip()
        ext = p.suffix.lower().strip()
        clean_stem = re.sub(r"[^\w\-]+", "-", stem)
        clean_stem = re.sub(r"-+", "-", clean_stem).strip("-")
        return f"{clean_stem}{ext}"
