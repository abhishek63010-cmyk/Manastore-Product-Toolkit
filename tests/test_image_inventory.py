from pathlib import Path
from zipfile import ZipFile
import pytest
from src.images.inventory import (
    DiscoveredImage,
    ImageInventory,
    natural_sort_key,
)


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_natural_sort_key():
    """Verify natural sorting key handles numeric increments properly."""
    filenames = [
        "img10.png",
        "img1.png",
        "img2.png",
        "img20.png",
        "img3.png",
    ]
    sorted_files = sorted(filenames, key=natural_sort_key)
    assert sorted_files == [
        "img1.png",
        "img2.png",
        "img3.png",
        "img10.png",
        "img20.png",
    ]


def test_folder_image_discovery(tmp_path):
    """Verify image discovery in a flat directory."""
    (tmp_path / "photo1.jpg").write_bytes(b"dummy_jpg")
    (tmp_path / "photo2.png").write_bytes(b"dummy_png")
    (tmp_path / "photo3.webp").write_bytes(b"dummy_webp")
    (tmp_path / "notes.txt").write_text("not an image", encoding="utf-8")

    inventory = ImageInventory.discover_from_directory(tmp_path)
    assert inventory.total_images == 3
    assert len(inventory.ignored_files) == 1
    assert inventory.ignored_files[0]["filename"] == "notes.txt"

    names = [img.original_filename for img in inventory]
    assert names == ["photo1.jpg", "photo2.png", "photo3.webp"]


def test_nested_folder_image_discovery(tmp_path):
    """Verify recursive image discovery across nested folder structures."""
    sub1 = tmp_path / "CatalogA" / "Red"
    sub2 = tmp_path / "CatalogB" / "Blue"
    sub1.mkdir(parents=True)
    sub2.mkdir(parents=True)

    (sub1 / "A-01.jpg").write_bytes(b"data")
    (sub1 / "A-02.png").write_bytes(b"data")
    (sub2 / "B-01.jpeg").write_bytes(b"data")
    (sub2 / "data.csv").write_text("a,b,c", encoding="utf-8")

    inventory = ImageInventory.discover(tmp_path, recursive=True)
    assert inventory.total_images == 3
    assert len(inventory.ignored_files) == 1

    rel_paths = [img.relative_path for img in inventory]
    assert "CatalogA/Red/A-01.jpg" in rel_paths
    assert "CatalogA/Red/A-02.png" in rel_paths
    assert "CatalogB/Blue/B-01.jpeg" in rel_paths


def test_zip_image_discovery(tmp_path):
    """Verify discovery of images directly from a ZIP archive."""
    zip_path = tmp_path / "catalog_images.zip"
    with ZipFile(zip_path, "w") as zf:
        zf.writestr("images/item_1.jpg", b"fake_jpeg")
        zf.writestr("images/item_2.png", b"fake_png")
        zf.writestr("images/item_10.jpg", b"fake_jpeg_10")
        zf.writestr("metadata.json", b"{}")

    inventory = ImageInventory.discover(zip_path)
    assert inventory.total_images == 3
    assert len(inventory.ignored_files) == 1

    # Verify natural sort inside zip
    filenames = [img.original_filename for img in inventory]
    assert filenames == ["item_1.jpg", "item_2.png", "item_10.jpg"]


def test_existing_workspace_zip_fixture(project_root):
    """Verify discovery on the existing committed Amra zip fixture if present."""
    zip_fixture = (
        project_root
        / "input"
        / "AMRA-RAJTEX-001"
        / "rajtex-ajrakh-vol-35-modal-satin-printed-saree-set.zip"
    )
    if zip_fixture.exists():
        inventory = ImageInventory.discover(zip_fixture)
        assert inventory.total_images == 7
        assert all(img.file_extension == ".jpg" for img in inventory)


def test_deterministic_image_identity(tmp_path):
    """Verify identical files in repeated runs generate the exact same stable image_id."""
    img_path = tmp_path / "product_shot.png"
    img_path.write_bytes(b"pixels")

    inv1 = ImageInventory.discover(tmp_path)
    inv2 = ImageInventory.discover(tmp_path)

    assert inv1.images[0].image_id == inv2.images[0].image_id
    assert inv1.images[0].image_id.startswith("img_")
