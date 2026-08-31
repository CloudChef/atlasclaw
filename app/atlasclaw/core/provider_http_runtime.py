# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Provider-declared HTTP route manifests and dynamic runtime invocation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path, PurePosixPath
import re
import sys
from types import ModuleType
from typing import Any, Mapping, Optional

from pydantic_ai import Agent, BinaryContent


RUNTIME_API_FILENAME = "runtime-api.json"
_ALLOWED_METHODS = frozenset({"DELETE", "GET", "PATCH", "POST", "PUT"})
_ALLOWED_ACCESS_POLICIES = frozenset({"admin", "provider_user"})
_PATH_PARAMETER = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_STATIC_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9._~-]+$")
_PYTHON_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RUNTIME_CACHE: dict[Path, tuple[int, ModuleType]] = {}


class ProviderHttpRuntimeError(RuntimeError):
    """Represent a provider-runtime failure safe for Core's HTTP error mapper.

    Instances carry a bounded public detail, HTTP status, and stable error code;
    provider exceptions that do not implement this contract remain generic 500s.
    """

    def __init__(
        self,
        detail: str,
        *,
        status_code: int = 500,
        code: str = "provider_http_runtime_failed",
    ) -> None:
        """Create an error whose status, code, and detail can be mapped by the API layer."""
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ProviderHttpRouteDefinition:
    """Describe one immutable provider-owned route from ``runtime-api.json``.

    Registry construction validates every field before this descriptor becomes
    discoverable. ``match`` performs only deterministic method/path matching and
    never imports code or consults mutable instance configuration.
    """

    provider_type: str
    method: str
    path: str
    module_path: Path
    handler: str
    capability: str
    access: str
    success_status: int = 200
    _segments: tuple[str, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Normalize the route once so request matching does not reparse its manifest."""
        object.__setattr__(self, "provider_type", self.provider_type.strip().lower())
        object.__setattr__(self, "method", self.method.strip().upper())
        object.__setattr__(self, "path", self.path.strip())
        object.__setattr__(self, "handler", self.handler.strip())
        object.__setattr__(self, "capability", self.capability.strip().lower())
        object.__setattr__(self, "access", self.access.strip().lower())
        object.__setattr__(self, "_segments", tuple(self.path.split("/")))

    @property
    def static_segment_count(self) -> int:
        """Return the literal-segment count used for deterministic route precedence.

        Routes with more static segments sort before equally long parameterized routes.
        """
        return sum(1 for segment in self._segments if _PATH_PARAMETER.fullmatch(segment) is None)

    def match(self, method: str, runtime_path: str) -> Optional[dict[str, str]]:
        """Match an incoming method and normalized relative provider path.

        Args:
            method: Incoming HTTP method; comparison is case-insensitive.
            runtime_path: Dispatcher path below the provider instance prefix.

        Returns:
            Captured path parameters for a complete match, or ``None`` when the
            method, segment count, or any literal segment differs.
        """
        if self.method != str(method or "").strip().upper():
            return None
        normalized_path = runtime_path.strip("/")
        if not normalized_path or "//" in normalized_path:
            return None
        incoming = tuple(normalized_path.split("/"))
        if len(incoming) != len(self._segments):
            return None
        parameters: dict[str, str] = {}
        for declared, actual in zip(self._segments, incoming):
            parameter_match = _PATH_PARAMETER.fullmatch(declared)
            if parameter_match is not None:
                parameters[parameter_match.group(1)] = actual
            elif declared != actual:
                return None
        return parameters


@dataclass(frozen=True)
class ProviderHttpManifest:
    """Contain all validated HTTP routes published by one provider package.

    The registry installs this immutable value only after every route module and
    handler contract succeeds, so capability discovery can fail closed per package.
    """

    provider_type: str
    routes: tuple[ProviderHttpRouteDefinition, ...]


class ProviderVisualRuntime:
    """Expose the active Agent model through a narrow visual-input contract.

    Providers supply prompts and binary semantics. Core retains model ownership,
    enforces the timeout, and maps model/transport failures without exposing the
    broader ``AgentRunner`` or request dependency graph.
    """

    def __init__(self, agent_runner: Any) -> None:
        """Capture the active model without exposing AgentRunner internals to providers."""
        agent = getattr(agent_runner, "agent", None)
        self._model = getattr(agent, "model", None)
        self.model_name = str(getattr(self._model, "model_name", "") or "").strip()
        self._agents: dict[str, Agent[Any, str]] = {}

    @property
    def available(self) -> bool:
        """Return whether a configured Agent model can receive visual operations.

        This indicates model presence only; provider-specific format capability is
        still validated by the provider converter.
        """
        return self._model is not None

    async def run_visual(
        self,
        *,
        data: bytes,
        media_type: str,
        prompt: str,
        system_prompt: str,
        identifier: str,
        timeout_seconds: float,
    ) -> str:
        """Run one provider-supplied visual prompt with the active Agent model.

        The provider owns extraction semantics and prompts. Core owns only model access,
        timeout enforcement, and the binary-input transport used by Pydantic AI.

        Args:
            data: Bounded binary image or rendered page supplied by the provider.
            media_type: MIME type passed to the model transport.
            prompt: Provider-owned task prompt for this asset.
            system_prompt: Provider-owned system contract used to cache a narrow agent.
            identifier: Safe asset label included in diagnostic errors.
            timeout_seconds: Remaining provider request budget for this model call.

        Returns:
            Text output produced by the active model.

        Raises:
            ProviderHttpRuntimeError: If no model is configured, the deadline expires,
                or visual model execution fails.
        """
        if self._model is None:
            raise ProviderHttpRuntimeError(
                "A visual-capable Agent model is required for this provider operation",
                status_code=503,
                code="visual_model_unavailable",
            )
        visual_agent = self._agents.get(system_prompt)
        if visual_agent is None:
            visual_agent = Agent(
                self._model,
                output_type=str,
                system_prompt=system_prompt,
                retries=1,
            )
            self._agents[system_prompt] = visual_agent
        binary = BinaryContent(data=data, media_type=media_type, identifier=identifier)
        try:
            result = await asyncio.wait_for(
                visual_agent.run([prompt, binary]),
                timeout=max(float(timeout_seconds), 0.1),
            )
        except TimeoutError as exc:
            raise ProviderHttpRuntimeError(
                f"Provider visual processing timed out for {identifier}",
                status_code=504,
                code="visual_model_timeout",
            ) from exc
        except Exception as exc:
            raise ProviderHttpRuntimeError(
                f"Provider visual processing failed for {identifier}: {type(exc).__name__}",
                status_code=502,
                code="visual_model_failed",
            ) from exc
        return str(result.output or "")


@dataclass
class ProviderHttpContext:
    """Supply one authorized request with immutable routing and actor context.

    Core constructs this object only after route access and instance RBAC checks.
    Providers own validation of request/domain fields and may use the optional visual
    bridge without receiving authentication secrets or ``AgentRunner`` internals.
    """

    provider_type: str
    provider_instance: str
    provider_config: dict[str, Any]
    path_params: dict[str, str]
    user_id: str
    tenant_id: str
    is_admin: bool
    token_id: str = ""
    visual_runtime: Optional[ProviderVisualRuntime] = None

    @property
    def visual_model_name(self) -> str:
        """Return the configured visual model name without exposing the model object.

        An empty value means the request has no active visual runtime.
        """
        return self.visual_runtime.model_name if self.visual_runtime is not None else ""

    @property
    def visual_available(self) -> bool:
        """Return whether this request can submit input through the visual bridge.

        Providers must still enforce their own supported media types and limits.
        """
        return bool(self.visual_runtime is not None and self.visual_runtime.available)

    async def run_visual(
        self,
        *,
        data: bytes,
        media_type: str,
        prompt: str,
        system_prompt: str,
        identifier: str,
        timeout_seconds: float,
    ) -> str:
        """Delegate bounded provider visual input through Core's model bridge.

        Args:
            data: Provider-validated binary image or rendered page.
            media_type: MIME type supplied to the model transport.
            prompt: Provider-owned prompt for the asset.
            system_prompt: Provider-owned conversion contract.
            identifier: Safe asset label used in errors.
            timeout_seconds: Remaining request budget for the model call.

        Returns:
            Text output from the active model.

        Raises:
            ProviderHttpRuntimeError: If no visual runtime is available or the
                delegated operation fails.
        """
        if self.visual_runtime is None:
            raise ProviderHttpRuntimeError(
                "A visual-capable Agent model is required for this provider operation",
                status_code=503,
                code="visual_model_unavailable",
            )
        return await self.visual_runtime.run_visual(
            data=data,
            media_type=media_type,
            prompt=prompt,
            system_prompt=system_prompt,
            identifier=identifier,
            timeout_seconds=timeout_seconds,
        )


def _safe_runtime_module_path(provider_root: Path, value: Any, manifest_path: Path) -> Path:
    """Resolve a provider module while rejecting absolute, escaping, and symlink paths."""
    raw_path = str(value or "").strip()
    candidate = PurePosixPath(raw_path)
    if (
        not raw_path
        or candidate.is_absolute()
        or ".." in candidate.parts
        or candidate.suffix != ".py"
    ):
        raise ValueError(f"{manifest_path}: route module must be a safe relative .py path")
    lexical_path = provider_root.joinpath(*candidate.parts)
    if lexical_path.is_symlink():
        raise ValueError(f"{manifest_path}: route module must not be a symlink")
    resolved_root = provider_root.resolve()
    resolved_path = lexical_path.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"{manifest_path}: route module escapes provider root") from exc
    if not resolved_path.is_file():
        raise ValueError(f"{manifest_path}: route module does not exist: {raw_path}")
    return resolved_path


def _validate_runtime_path(value: Any, manifest_path: Path) -> str:
    """Validate a relative route template containing literal and named segments."""
    path = str(value or "").strip()
    if not path or path.startswith("/") or path.endswith("/") or "//" in path:
        raise ValueError(f"{manifest_path}: route path must be a non-empty relative path")
    parameter_names: set[str] = set()
    for segment in path.split("/"):
        parameter_match = _PATH_PARAMETER.fullmatch(segment)
        if parameter_match is not None:
            name = parameter_match.group(1)
            if name in parameter_names:
                raise ValueError(f"{manifest_path}: duplicate route parameter {name!r}")
            parameter_names.add(name)
            continue
        if not _STATIC_PATH_SEGMENT.fullmatch(segment) or segment in {".", ".."}:
            raise ValueError(f"{manifest_path}: invalid route path segment {segment!r}")
    return path


def _route_templates_overlap(left: str, right: str) -> bool:
    """Return whether two equal-length templates can match the same request path."""
    left_segments = left.split("/")
    right_segments = right.split("/")
    if len(left_segments) != len(right_segments):
        return False
    for left_segment, right_segment in zip(left_segments, right_segments):
        left_parameter = _PATH_PARAMETER.fullmatch(left_segment) is not None
        right_parameter = _PATH_PARAMETER.fullmatch(right_segment) is not None
        if not left_parameter and not right_parameter and left_segment != right_segment:
            return False
    return True


def load_provider_http_manifest(
    provider_root: Path,
    *,
    provider_type: str,
    runtime_capabilities: tuple[str, ...],
) -> Optional[ProviderHttpManifest]:
    """Load and validate a provider's optional fixed-name HTTP route manifest.

    Args:
        provider_root: Trusted provider package directory containing the optional
            ``runtime-api.json`` and provider-owned handler modules.
        provider_type: Registry identity that every returned route is bound to.
        runtime_capabilities: Capabilities declared by ``provider.schema.json``;
            each HTTP route must reference one of these values.

    Returns:
        A fully validated immutable manifest, or ``None`` when the provider does
        not publish an HTTP runtime manifest.

    Raises:
        ValueError: If the manifest, route declarations, module paths, async
            handlers, or ``(request, context)`` handler signatures are invalid.
    """
    provider_root = Path(provider_root)
    manifest_path = provider_root / RUNTIME_API_FILENAME
    if not manifest_path.is_file():
        return None
    try:
        raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{manifest_path}: invalid JSON: {exc}") from exc
    if not isinstance(raw_manifest, Mapping):
        raise ValueError(f"{manifest_path}: runtime API manifest must be an object")
    if raw_manifest.get("schema_version") != 1:
        raise ValueError(
            f"{manifest_path}: unsupported schema_version {raw_manifest.get('schema_version')!r}"
        )
    raw_routes = raw_manifest.get("routes")
    if not isinstance(raw_routes, list) or not raw_routes:
        raise ValueError(f"{manifest_path}: routes must be a non-empty list")

    normalized_provider_type = str(provider_type or "").strip().lower()
    declared_capabilities = {str(item).strip().lower() for item in runtime_capabilities}
    routes: list[ProviderHttpRouteDefinition] = []
    for raw_route in raw_routes:
        if not isinstance(raw_route, Mapping):
            raise ValueError(f"{manifest_path}: route entries must be objects")
        method = str(raw_route.get("method") or "").strip().upper()
        if method not in _ALLOWED_METHODS:
            raise ValueError(f"{manifest_path}: unsupported route method {method!r}")
        path = _validate_runtime_path(raw_route.get("path"), manifest_path)
        overlapping_route = next(
            (
                route
                for route in routes
                if route.method == method and _route_templates_overlap(route.path, path)
            ),
            None,
        )
        if overlapping_route is not None:
            raise ValueError(
                f"{manifest_path}: ambiguous overlapping routes "
                f"{method} {overlapping_route.path} and {method} {path}"
            )
        capability = str(raw_route.get("capability") or "").strip().lower()
        if capability not in declared_capabilities:
            raise ValueError(
                f"{manifest_path}: route capability {capability!r} is not declared by provider.schema.json"
            )
        access = str(raw_route.get("access") or "").strip().lower()
        if access not in _ALLOWED_ACCESS_POLICIES:
            raise ValueError(f"{manifest_path}: unsupported route access policy {access!r}")
        handler = str(raw_route.get("handler") or "").strip()
        if _PYTHON_IDENTIFIER.fullmatch(handler) is None:
            raise ValueError(f"{manifest_path}: route handler must be a Python identifier")
        try:
            success_status = int(raw_route.get("success_status", 200))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{manifest_path}: success_status must be an integer") from exc
        if success_status < 200 or success_status > 299:
            raise ValueError(f"{manifest_path}: success_status must be a 2xx status")
        routes.append(
            ProviderHttpRouteDefinition(
                provider_type=normalized_provider_type,
                method=method,
                path=path,
                module_path=_safe_runtime_module_path(
                    provider_root,
                    raw_route.get("module"),
                    manifest_path,
                ),
                handler=handler,
                capability=capability,
                access=access,
                success_status=success_status,
            )
        )
    for route in routes:
        try:
            module = _load_provider_http_module(route.module_path)
        except Exception as exc:
            raise ValueError(
                f"{manifest_path}: cannot load route module {route.module_path.name}: {exc}"
            ) from exc
        handler = getattr(module, route.handler, None)
        if not inspect.iscoroutinefunction(handler):
            raise ValueError(
                f"{manifest_path}: route handler does not exist or is not async: {route.handler}"
            )
        try:
            inspect.signature(handler).bind(object(), object())
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{manifest_path}: route handler must accept (request, context): {route.handler}"
            ) from exc
    routes.sort(key=lambda route: (-route.static_segment_count, route.path, route.method))
    return ProviderHttpManifest(provider_type=normalized_provider_type, routes=tuple(routes))


def _load_provider_http_module(module_path: Path) -> ModuleType:
    """Load and cache a provider runtime module inside an isolated synthetic package."""
    resolved_path = Path(module_path).resolve()
    if not resolved_path.is_file() or resolved_path.is_symlink():
        raise ProviderHttpRuntimeError("Provider HTTP runtime module is unavailable", status_code=503)
    mtime_ns = resolved_path.stat().st_mtime_ns
    cached = _RUNTIME_CACHE.get(resolved_path)
    if cached is not None and cached[0] == mtime_ns:
        return cached[1]

    provider_root = resolved_path.parent
    package_suffix = hashlib.sha256(str(provider_root).encode("utf-8")).hexdigest()[:16]
    module_suffix = hashlib.sha256(str(resolved_path).encode("utf-8")).hexdigest()[:16]
    package_name = f"atlasclaw_provider_http_{package_suffix}"
    package = sys.modules.get(package_name)
    if package is None:
        package = ModuleType(package_name)
        package.__package__ = package_name
        package.__path__ = [str(provider_root)]  # type: ignore[attr-defined]
        sys.modules[package_name] = package
    module_name = f"{package_name}.runtime_{module_suffix}"
    spec = importlib.util.spec_from_file_location(module_name, resolved_path)
    if spec is None or spec.loader is None:
        raise ProviderHttpRuntimeError("Cannot load provider HTTP runtime", status_code=503)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    _RUNTIME_CACHE[resolved_path] = (mtime_ns, module)
    return module


async def invoke_provider_http_handler(
    route: ProviderHttpRouteDefinition,
    request: Any,
    context: ProviderHttpContext,
) -> Any:
    """Invoke one registered provider handler through Core's generic call contract.

    Args:
        route: Route definition previously validated during provider discovery.
        request: Bounded replayable request whose body spool remains owned by Core.
        context: Authorized instance, actor, path-parameter, and visual-runtime context.

    Returns:
        The eager JSON-compatible value returned by the provider handler.

    Raises:
        ProviderHttpRuntimeError: If the registered module or async handler is no
            longer available at dispatch time.
        Exception: Provider-domain failures are intentionally propagated for the
            API dispatcher to map without embedding provider semantics in Core.
    """
    module = _load_provider_http_module(route.module_path)
    handler = getattr(module, route.handler, None)
    if not inspect.iscoroutinefunction(handler):
        raise ProviderHttpRuntimeError(
            f"Provider HTTP handler is unavailable: {route.handler}",
            status_code=503,
            code="provider_http_handler_unavailable",
        )
    return await handler(request, context)
