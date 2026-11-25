"""Developer-friendly `@tool` decorator with automatic Pydantic schema inference."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, cast, get_type_hints, overload

from pydantic import BaseModel, ConfigDict, create_model

from orqen.exceptions import ToolValidationError
from orqen.tools.types import Tool, ToolExecutionMode

if TYPE_CHECKING:
    from collections.abc import Callable


def _build_input_model(fn: Callable[..., Any], tool_name: str) -> type[BaseModel]:
    """Infer a strict Pydantic `BaseModel` from `fn`'s signature and type hints."""
    sig = inspect.signature(fn)
    try:
        hints = get_type_hints(fn)
    except Exception:
        hints = {}

    fields: dict[str, tuple[Any, Any]] = {}
    for param_name, param in sig.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            raise ToolValidationError(
                f"Tool '{tool_name}' cannot use *args or **kwargs parameter '{param_name}'.",
                details={"tool": tool_name, "parameter": param_name},
            )
        annotation = hints.get(param_name, Any)
        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[param_name] = (annotation, default)

    model_name = f"{tool_name.title().replace('_', '')}Input"
    return cast(
        "type[BaseModel]",
        create_model(
            model_name,
            __config__=ConfigDict(extra="forbid"),
            **fields,  # type: ignore[call-overload]
        ),
    )


@overload
def tool(fn: Callable[..., Any]) -> Tool: ...


@overload
def tool(
    fn: None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    timeout_seconds: float | None = None,
    dangerous: bool = False,
    permissions: tuple[str, ...] = (),
    metadata: dict[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], Tool]: ...


def tool(
    fn: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    timeout_seconds: float | None = None,
    dangerous: bool = False,
    permissions: tuple[str, ...] = (),
    metadata: dict[str, Any] | None = None,
) -> Tool | Callable[[Callable[..., Any]], Tool]:
    """Decorate an async or sync function as an ORQEN `Tool`."""

    def _wrap(target: Callable[..., Any]) -> Tool:
        if not callable(target):
            raise ToolValidationError("The @tool decorator target must be callable.")
        resolved_name = name if name is not None else str(getattr(target, "__name__", ""))
        doc = inspect.getdoc(target) or ""
        resolved_description = description if description is not None else doc
        mode: ToolExecutionMode = "async" if inspect.iscoroutinefunction(target) else "sync"
        input_model = _build_input_model(target, resolved_name)

        return Tool(
            name=resolved_name,
            description=resolved_description,
            fn=target,
            input_model=input_model,
            mode=mode,
            timeout_seconds=timeout_seconds,
            dangerous=dangerous,
            permissions=permissions,
            metadata=metadata,
        )

    if fn is not None:
        return _wrap(fn)
    return _wrap
