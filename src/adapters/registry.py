from src.adapters.amra import AmraAdapter
from src.adapters.base import BaseSupplierAdapter
from src.adapters.generic import GenericTabularAdapter
from src.extractors.raw_record import RawTabularData


class AdapterRegistry:
    """
    Registry for supplier normalization adapters.
    Supports explicit adapter selection by name or automatic detection via can_handle().
    """

    _registry: dict[str, BaseSupplierAdapter] = {}

    @classmethod
    def register(cls, adapter: BaseSupplierAdapter) -> None:
        """Register an adapter instance."""
        cls._registry[adapter.adapter_name.lower()] = adapter

    @classmethod
    def get_adapter(
        cls,
        supplier_name: str | None = None,
        raw_data: RawTabularData | None = None,
    ) -> BaseSupplierAdapter:
        """
        Retrieve adapter by explicit supplier name or auto-detect from raw tabular data.
        """
        cls._ensure_defaults_registered()

        # 1. Explicit supplier name lookup
        if supplier_name and supplier_name.strip():
            name_clean = supplier_name.strip().lower().replace("-", "_")
            
            # Check exact name or common aliases
            alias_map = {
                "amra": "amra_adapter",
                "amra_wholesale": "amra_adapter",
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

        # 2. Auto-detection from raw data
        if raw_data is not None:
            # Check specialized adapters first (e.g. Amra before Generic)
            specialized_adapters = [
                a for a in cls._registry.values()
                if a.adapter_name != "generic_tabular"
            ]
            for adapter in specialized_adapters:
                if adapter.can_handle(raw_data):
                    return adapter

            # Fallback to generic tabular adapter
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
            cls.register(GenericTabularAdapter())


# Initialize default adapters
AdapterRegistry._ensure_defaults_registered()
