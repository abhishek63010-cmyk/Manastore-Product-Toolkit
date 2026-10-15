from pathlib import Path

from src.adapters.amra import AmraAdapter
from src.adapters.base import BaseSupplierAdapter
from src.adapters.generic import GenericTabularAdapter
from src.adapters.mudhra_ethnic import MudhraEthnicAdapter
from src.extractors.raw_record import RawTabularData


class AdapterRegistry:
    """
    Registry for supplier normalization adapters.
    Supports explicit adapter selection by name or automatic detection via can_handle().
    """

    _registry: dict[str, BaseSupplierAdapter] = {}

    @staticmethod
    def detect_supplier_from_path(source_path: str | Path | None) -> str | None:
        """Detect the supplier from the actual ancestor path, not incidental OCR text or lookup substrings."""
        if source_path is None:
            return None

        path = Path(source_path)
        for ancestor in [path, *path.parents]:
            name = ancestor.name.strip().lower().replace("-", "_")
            if name in {"amra", "amra_wholesale"}:
                return "amra_wholesale"
            if name in {"mudhra", "mudhra_ethnic"}:
                return "mudhra_ethnic"
        return None

    @classmethod
    def register(cls, adapter: BaseSupplierAdapter) -> None:
        """Register an adapter instance."""
        cls._registry[adapter.adapter_name.lower()] = adapter

    @classmethod
    def get_adapter(
        cls,
        supplier_name: str | None = None,
        raw_data: RawTabularData | None = None,
        source_path: str | Path | None = None,
    ) -> BaseSupplierAdapter:
        """
        Retrieve adapter by explicit supplier name or auto-detect from source path/raw tabular data.
        """
        cls._ensure_defaults_registered()

        source_hint = cls.detect_supplier_from_path(source_path)
        if supplier_name and supplier_name.strip():
            supplier_name = supplier_name.strip()
        elif source_hint:
            supplier_name = source_hint

        # 1. Explicit supplier name lookup
        if supplier_name and supplier_name.strip():
            name_clean = supplier_name.strip().lower().replace("-", "_")

            alias_map = {
                "amra": "amra_adapter",
                "amra_wholesale": "amra_adapter",
                "mudhra": "mudhra_ethnic",
                "mudhra_ethnic": "mudhra_ethnic",
                "mudhra_ethnic_adapter": "mudhra_ethnic",
                "generic": "generic_tabular",
                "tabular": "generic_tabular",
                "default": "generic_tabular",
            }
            target_key = alias_map.get(name_clean, name_clean)

            if target_key in cls._registry:
                return cls._registry[target_key]

            for key, adapter in cls._registry.items():
                if name_clean in key or key in name_clean:
                    return adapter

            raise ValueError(
                f"No registered adapter found for supplier '{supplier_name}'. "
                f"Available adapters: {list(cls._registry.keys())}"
            )

        # 2. Auto-detection from source path first (supplier folder names are authoritative)
        if source_hint:
            if source_hint in {"amra", "amra_wholesale"}:
                return cls._registry["amra_adapter"]
            if source_hint == "mudhra_ethnic" or source_hint == "mudhra":
                return cls._registry["mudhra_ethnic"]

        # 3. Auto-detection from raw data
        if raw_data is not None:
            specialized_adapters = [
                a for a in cls._registry.values()
                if a.adapter_name != "generic_tabular"
            ]
            for adapter in specialized_adapters:
                if adapter.can_handle(raw_data):
                    return adapter

            generic_adapter = cls._registry.get("generic_tabular")
            if generic_adapter and generic_adapter.can_handle(raw_data):
                return generic_adapter

        raise ValueError(
            "Could not determine supplier adapter automatically. "
            "Please specify --supplier explicitly."
        )

    @classmethod
    def list_adapters(cls) -> list[str]:
        cls._ensure_defaults_registered()
        return list(cls._registry.keys())

    @classmethod
    def _ensure_defaults_registered(cls) -> None:
        if not cls._registry:
            cls.register(AmraAdapter())
            cls.register(MudhraEthnicAdapter())
            cls.register(GenericTabularAdapter())


# Initialize default adapters
AdapterRegistry._ensure_defaults_registered()
