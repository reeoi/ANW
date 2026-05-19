"""Step contract schema — Pydantic v2 models for step manifests.

Phase 1 of WORKFLOW_GENERATOR_PLAN.md: defines what a step looks like.
No runtime logic here — just schema + validation.
"""

from __future__ import annotations

from enum import Enum, StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ── Enums ──────────────────────────────────────────────────────────────────


class PortType(str, Enum):
    """Data types that ports can carry."""
    text = "text"
    markdown = "markdown"
    json = "json"
    url = "url"
    datetime = "datetime"
    int = "int"
    float = "float"
    bool = "bool"
    path = "path"
    bytes = "bytes"
    any = "any"


class ExecutorKind(str, Enum):
    """How a step is actually executed."""
    builtin = "builtin"
    action_chain = "action_chain"
    external_cmd = "external_cmd"


StepCategory = Literal["输入", "处理", "审核", "输出", "工具"]
Permission = Literal["fs.read", "fs.write", "net.http", "browser.cdp", "llm.call"]
FailureStrategy = Literal["stop", "skip", "retry", "fallback"]
Provenance = Literal["builtin", "user", "ai"]


# ── Models ─────────────────────────────────────────────────────────────────


class Port(BaseModel):
    """A typed input/output/param port for a step."""
    model_config = ConfigDict(populate_by_name=True)

    name: str
    type: PortType
    required: bool = True
    description: str = ""
    default: Any = None


class Executor(BaseModel):
    """How to run the step. Exactly one of ref/actions/cmd_template must be set,
    matching the `kind` field."""
    model_config = ConfigDict(populate_by_name=True)

    kind: ExecutorKind
    ref: str | None = None         # builtin → "module.func"
    actions: list[dict] | None = None   # action_chain
    cmd_template: str | None = None     # external_cmd


class StepManifest(BaseModel):
    """Complete declaration of a single pipeline step."""
    model_config = ConfigDict(populate_by_name=True)

    # identity
    id: str
    version: str = "v1"
    name: str
    category: StepCategory
    description: str = ""

    # ports
    inputs: list[Port] = Field(default_factory=list)
    outputs: list[Port] = Field(default_factory=list)
    params: list[Port] = Field(default_factory=list)

    # execution
    executor: Executor
    preconditions: list[str] = Field(default_factory=list)
    permissions: list[Permission] = Field(default_factory=list)
    estimated_duration_seconds: int | None = None
    estimated_cost_cny: float | None = None
    timeout_seconds: int = 300
    max_cost_cny: float | None = None

    # failure handling
    on_failure: FailureStrategy = "stop"
    retry_max: int = 0
    fallback_step_id: str | None = None

    # metadata
    fixtures: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance = "builtin"
    tags: list[str] = Field(default_factory=list)
    outputs_dynamic: bool = False  # True if outputs are determined at runtime

    @field_validator("id")
    @classmethod
    def id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("step id must not be empty")
        return v.strip()

    @model_validator(mode="after")
    def validate_self(self) -> "StepManifest":
        """Cross-field validation rules."""
        # input/output name uniqueness
        in_names = {p.name for p in self.inputs}
        out_names = {p.name for p in self.outputs}
        param_names = {p.name for p in self.params}
        overlap = in_names & out_names
        if overlap:
            raise ValueError(f"input/output port names must not overlap: {overlap}")
        overlap = in_names & param_names
        if overlap:
            raise ValueError(f"input/param port names must not overlap: {overlap}")

        # executor consistency
        if self.executor.kind == ExecutorKind.builtin:
            if not self.executor.ref:
                raise ValueError("builtin executor requires 'ref'")
        elif self.executor.kind == ExecutorKind.action_chain:
            if not self.executor.actions:
                raise ValueError("action_chain executor requires 'actions'")
        elif self.executor.kind == ExecutorKind.external_cmd:
            if not self.executor.cmd_template:
                raise ValueError("external_cmd executor requires 'cmd_template'")

        return self


__all__ = [
    "Executor", "ExecutorKind", "FailureStrategy", "Permission",
    "Port", "PortType", "Provenance", "StepCategory", "StepManifest",
]
