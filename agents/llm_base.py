from __future__ import annotations

import abc
from abc import abstractmethod
from typing import get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo


class LLMBaseModel(BaseModel, abc.ABC):
    """Base model for LLM-parseable response types."""

    @abstractmethod
    def llm_str(self):
        raise NotImplementedError("LLM String has to be implemented.")

    @classmethod
    def _is_field_hidden(cls, fvalue: FieldInfo) -> bool:
        if fvalue.exclude:
            return True
        extra = fvalue.json_schema_extra
        if isinstance(extra, dict):
            return bool(extra.get("hidden"))
        return False

    @classmethod
    def _excluded_fields(cls, include_hidden: bool = False) -> set[str]:
        if include_hidden:
            return set()
        names: set[str] = set()
        for klass in cls.__mro__:
            if hasattr(klass, "model_fields"):
                for fname, finfo in klass.model_fields.items():
                    if cls._is_field_hidden(finfo):
                        names.add(fname)
        return names

    @classmethod
    def _resolve_excluded_by_title(cls, include_hidden: bool = False) -> dict[str, set[str]]:
        seen: set[type] = set()
        result: dict[str, set[str]] = {}

        def walk(model: type) -> None:
            if model in seen or not hasattr(model, "model_fields"):
                return
            seen.add(model)
            title = getattr(model, "__name__", "")
            excluded = model._excluded_fields(include_hidden)  # type: ignore[attr-defined]
            if excluded:
                result[title] = excluded
            for finfo in getattr(model, "model_fields", {}).values():
                ann = finfo.annotation
                for candidate in getattr(ann, "__args__", [ann]):
                    if isinstance(candidate, type) and issubclass(candidate, LLMBaseModel):
                        walk(candidate)  # type: ignore[arg-type]

        walk(cls)
        return result

    @classmethod
    def _extractor_fields(cls, indent: str = "  ", include_hidden: bool = False) -> str:
        parts: list[str] = []
        for fname, fvalue in cls.model_fields.items():
            if cls._is_field_hidden(fvalue) and not include_hidden:
                continue
            ftype = fvalue.annotation
            if get_origin(ftype) is list:
                if ftype is not None and hasattr(ftype, "__args__"):
                    inner = ftype.__args__[0]
                    if isinstance(inner, type) and issubclass(inner, LLMBaseModel):
                        parts.append(
                            f"{indent}- {fname}: a list, where each item has:\n{inner._extractor_fields(indent + '  ', include_hidden)}"
                        )
                        continue
                parts.append(f"{indent}- {fname}: {fvalue.description}")
            elif isinstance(ftype, type) and issubclass(ftype, LLMBaseModel):
                parts.append(ftype._extractor_fields(indent, include_hidden))
            else:
                parts.append(f"{indent}- {fname}: {fvalue.description}")
        return "\n".join(parts)

    @classmethod
    def extractor_str(cls, include_hidden: bool = False) -> str:
        title = cls.__name__
        fields = cls._extractor_fields(include_hidden=include_hidden)
        return (
            f"You are a JSON extraction expert. "
            f"Extract a valid JSON object of type `{title}` from the text below.\n"
            f"The JSON must have these fields:\n{fields}\n\n"
        )

    @classmethod
    def model_json_schema(
        cls,
        by_alias: bool = True,
        ref_template: str = "#/$defs/{model}",
        schema_generator: type | None = None,
        mode: str = "validation",
        include_hidden: bool = False,
        **kwargs,
    ) -> dict:
        call_kwargs: dict = {"by_alias": by_alias, "ref_template": ref_template, "mode": mode}
        if schema_generator is not None:
            call_kwargs["schema_generator"] = schema_generator
        call_kwargs.update(kwargs)
        schema = super().model_json_schema(**call_kwargs)
        excluded_by_title = cls._resolve_excluded_by_title(include_hidden)
        for title, excluded in excluded_by_title.items():
            defn = schema.get("$defs", {}).get(title)
            if isinstance(defn, dict) and "properties" in defn:
                defn["properties"] = {k: v for k, v in defn["properties"].items() if k not in excluded}
        own_excluded = cls._excluded_fields(include_hidden)
        if "properties" in schema:
            schema["properties"] = {k: v for k, v in schema["properties"].items() if k not in own_excluded}
        return schema
