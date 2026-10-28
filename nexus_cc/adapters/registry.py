"""Adapter registry — the fixed allowlist of executable adapters.

Adapters are registered here by class, never by arbitrary strings from task
input. The registry is the tool permission boundary: a task can only ever be
routed to a registered adapter, and only through its declared capability.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Type

from .base import Adapter
from ..errors import NotFoundError, ValidationError

# Built-in adapters land here (module names under nexus_cc.adapters).
_BUILTIN = [
    "artifact",
    "inspector",
    "echo",
]


class Registry:
    def __init__(self):
        self._adapters: dict[str, Type[Adapter]] = {}
        self._by_capability: dict[str, str] = {}

    def register(self, adapter_cls: Type[Adapter]) -> Type[Adapter]:
        name = adapter_cls.name
        if not name:
            raise ValidationError("adapter with empty name")
        if name in self._adapters:
            raise ValidationError("adapter already registered: %s" % name)
        self._adapters[name] = adapter_cls
        for cap in adapter_cls.capabilities:
            self._by_capability.setdefault(cap, name)
        return adapter_cls

    def resolve(self, name: str) -> Type[Adapter]:
        cls = self._adapters.get(name)
        if cls is None:
            raise NotFoundError("unknown adapter: %s" % name)
        return cls

    def list(self) -> list[dict]:
        out = []
        for name, cls in sorted(self._adapters.items()):
            out.append({
                "name": name,
                "description": cls.description,
                "capabilities": list(cls.capabilities),
                "default_requires_approval": cls.default_requires_approval,
                "supported_models": list(cls.supported_models),
            })
        return out

    def capability_owner(self, capability: str) -> str | None:
        return self._by_capability.get(capability)


def load_builtin_registry() -> Registry:
    reg = Registry()
    for module_name in _BUILTIN:
        mod = importlib.import_module(
            "nexus_cc.adapters.%s" % module_name
        )
        maker = getattr(mod, "register", None)
        if maker is None:
            # fallback: any Adapter subclass exported by the module
            for attr in vars(mod).values():
                if (isinstance(attr, type) and issubclass(attr, Adapter)
                        and attr is not Adapter and attr.name):
                    reg.register(attr)
            continue
        maker(reg)
    return reg
