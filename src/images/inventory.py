import hashlib
import re
from pathlib import Path
from typing import Any, Optional, Union
from zipfile import ZipFile, is_zipfile
from pydantic import BaseModel, Field


SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def natural_sort_key(s: str) -> list[Union[int, str]]:
    """
    Produce a sort key for natural sort ordering (e.g. image1, image2, image10).
    Case-insensitive and splits on numeric boundaries.
    """
    return [
        int(chunk) if chunk.isdigit() else chunk.lower()
        for chunk in re.split(r"(\d+)", s)
    ]


class DiscoveredImage(BaseModel):
    """
    Represents an image file discovered in a local folder or supplier ZIP archive.
    Has a deterministic identity and sorting key.
    """
    image_id: str = Field(
        ...,
        description="Stable deterministic identity hash based on source container and relative path"
    )
    source_type: str = Field(
        ...,
        description="'DIRECTORY' or 'ZIP'"
    )
    source_identity: str = Field(
        ...,
        description="Path or identifier of the source folder or ZIP archive"
    )
    relative_path: str = Field(
        ...,
        description="Path relative to source directory or archive root"
    )
    local_path: Optional[str] = Field(
        default=None,
        description="Absolute filesystem path if located on disk"
    )
    archive_member_path: Optional[str] = Field(
        default=None,
        description="Internal path inside the ZIP archive if located in a zip"
    )
    original_filename: str = Field(
        ...,
        description="Exact filename as extracted from source"
    )
    normalized_stem: str = Field(
        ...,
        description="Normalized filename stem without extension for token matching"
    )
    file_extension: str = Field(
        ...,
        description="File extension in lower case (e.g., '.jpg', '.png')"
    )
    file_size_bytes: int = Field(
        default=0,
        ge=0,
        description="File size in bytes"
    )
    tokens: list[str] = Field(
        default_factory=list,
        description="Normalized search tokens derived from path and filename"
    )

    @classmethod
    def create(
        cls,
        source_type: str,
        source_identity: str,
        relative_path: str,
        original_filename: str,
        file_extension: str,
        file_size_bytes: int = 0,
        local_path: Optional[str] = None,
        archive_member_path: Optional[str] = None,
    ) -> "DiscoveredImage":
        # Create deterministic stable ID: sha256(source_identity:relative_path)
        normalized_rel_path = relative_path.replace("\\", "/").strip("/")
        hash_input = f"{source_identity}:{normalized_rel_path}".encode("utf-8")
        image_id = f"img_{hashlib.sha256(hash_input).hexdigest()[:16]}"

        stem = Path(original_filename).stem
        norm_stem = re.sub(r"[^\w\-\.]+", "_", stem).strip("_").lower()

        # Tokenize relative path and filename for matching heuristics
        raw_tokens = re.split(r"[\/\s\-_,.]+", f"{normalized_rel_path}")
        tokens = [t.lower() for t in raw_tokens if t and len(t) >= 1]

        return cls(
            image_id=image_id,
            source_type=source_type,
            source_identity=source_identity,
            relative_path=normalized_rel_path,
            local_path=local_path,
            archive_member_path=archive_member_path,
            original_filename=original_filename,
            normalized_stem=norm_stem,
            file_extension=file_extension.lower(),
            file_size_bytes=file_size_bytes,
            tokens=tokens,
        )


class ImageInventory(BaseModel):
    """
    Collection of discovered images across folders or archives.
    Guarantees deterministic discovery and sorting order.
    """
    source_path: str = Field(
        ...,
        description="Root path or archive path from which images were discovered"
    )
    images: list[DiscoveredImage] = Field(
        default_factory=list,
        description="Discovered valid image assets sorted naturally"
    )
    ignored_files: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Files ignored during discovery (unsupported formats, system files)"
    )

    @property
    def total_images(self) -> int:
        return len(self.images)

    def __len__(self) -> int:
        return len(self.images)

    def __iter__(self):
        return iter(self.images)

    @classmethod
    def discover(cls, source_path: Union[str, Path], recursive: bool = True) -> "ImageInventory":
        """
        Main entrypoint: discovers images from a directory, nested directories, or ZIP archive.
        """
        path = Path(source_path)
        if not path.exists():
            raise FileNotFoundError(f"Image source path not found: {path}")

        if path.is_file() and is_zipfile(path):
            return cls.discover_from_zip(path)
        elif path.is_dir():
            return cls.discover_from_directory(path, recursive=recursive)
        else:
            raise ValueError(f"Source must be a valid directory or ZIP archive: {path}")

    @classmethod
    def discover_from_directory(cls, dir_path: Union[str, Path], recursive: bool = True) -> "ImageInventory":
        """Discover image files from a directory."""
        root = Path(dir_path).resolve()
        images: list[DiscoveredImage] = []
        ignored: list[dict[str, Any]] = []

        pattern = "**/*" if recursive else "*"
        all_files = [p for p in root.glob(pattern) if p.is_file()]

        # Sort paths naturally to guarantee deterministic discovery
        sorted_files = sorted(all_files, key=lambda p: natural_sort_key(str(p.relative_to(root))))

        for file_path in sorted_files:
            ext = file_path.suffix.lower()
            rel_path = str(file_path.relative_to(root)).replace("\\", "/")

            if ext in SUPPORTED_IMAGE_EXTENSIONS:
                img = DiscoveredImage.create(
                    source_type="DIRECTORY",
                    source_identity=str(root),
                    relative_path=rel_path,
                    original_filename=file_path.name,
                    file_extension=ext,
                    file_size_bytes=file_path.stat().st_size,
                    local_path=str(file_path),
                )
                images.append(img)
            else:
                ignored.append({
                    "relative_path": rel_path,
                    "filename": file_path.name,
                    "reason": f"Unsupported file extension '{ext}'",
                })

        return cls(
            source_path=str(root),
            images=images,
            ignored_files=ignored,
        )

    @classmethod
    def discover_from_zip(cls, zip_path: Union[str, Path]) -> "ImageInventory":
        """Discover image files inside a ZIP archive."""
        zip_p = Path(zip_path).resolve()
        images: list[DiscoveredImage] = []
        ignored: list[dict[str, Any]] = []

        with ZipFile(zip_p, "r") as archive:
            # Sort archive members naturally by filename
            sorted_items = sorted(
                archive.infolist(),
                key=lambda item: natural_sort_key(item.filename)
            )

            for item in sorted_items:
                if item.is_dir():
                    continue

                member_p = Path(item.filename)
                ext = member_p.suffix.lower()

                if ext in SUPPORTED_IMAGE_EXTENSIONS:
                    img = DiscoveredImage.create(
                        source_type="ZIP",
                        source_identity=str(zip_p),
                        relative_path=item.filename.replace("\\", "/"),
                        original_filename=member_p.name,
                        file_extension=ext,
                        file_size_bytes=item.file_size,
                        archive_member_path=item.filename,
                    )
                    images.append(img)
                else:
                    ignored.append({
                        "relative_path": item.filename,
                        "filename": member_p.name,
                        "reason": f"Unsupported file extension '{ext}'",
                    })

        return cls(
            source_path=str(zip_p),
            images=images,
            ignored_files=ignored,
        )

    @classmethod
    def from_source(cls, source_path: Union[str, Path], recursive: bool = True) -> "ImageInventory":
        """Alias for discover()."""
        return cls.discover(source_path=source_path, recursive=recursive)


# Alias for naming consistency
GenericImageInventory = ImageInventory

