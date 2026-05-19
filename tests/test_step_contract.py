"""Phase 1.4: Step contract schema tests."""
import json
import tempfile
from pathlib import Path

import pytest

from generator.c_pipeline.contract import (
    Executor,
    ExecutorKind,
    Port,
    PortType,
    StepManifest,
)
from generator.c_pipeline.contract_io import (
    dump_manifest,
    dump_to_dict,
    load_manifest,
)


def _make_builtin_step():
    return StepManifest(
        id="test_step",
        name="测试步骤",
        category="处理",
        description="A test step",
        inputs=[Port(name="text_in", type=PortType.markdown)],
        outputs=[Port(name="text_out", type=PortType.markdown)],
        executor=Executor(kind=ExecutorKind.builtin, ref="some.module.func"),
    )


# ── valid manifests ──

def test_valid_builtin_manifest():
    m = _make_builtin_step()
    assert m.id == "test_step"
    assert m.executor.kind == ExecutorKind.builtin
    assert m.executor.ref == "some.module.func"


def test_valid_action_chain_manifest():
    m = StepManifest(
        id="chain_step", name="链式步骤", category="工具",
        executor=Executor(kind=ExecutorKind.action_chain, actions=[{"action": "llm_call", "prompt": "hello"}]),
    )
    assert m.executor.actions[0]["action"] == "llm_call"


def test_valid_external_cmd_manifest():
    m = StepManifest(
        id="cmd_step", name="命令步骤", category="工具",
        executor=Executor(kind=ExecutorKind.external_cmd, cmd_template="python {script}"),
    )
    assert m.executor.cmd_template == "python {script}"


def test_defaults():
    m = StepManifest(
        id="d", name="defaults", category="输出",
        executor=Executor(kind=ExecutorKind.builtin, ref="a.b"),
    )
    assert m.version == "v1"
    assert m.timeout_seconds == 300
    assert m.on_failure == "stop"


# ── validation errors ──

def test_missing_id():
    with pytest.raises(ValueError):
        StepManifest(id="", name="bad", category="工具", executor=Executor(kind=ExecutorKind.builtin, ref="a.b"))


def test_port_name_overlap():
    with pytest.raises(ValueError):
        StepManifest(
            id="bad", name="重叠端口", category="工具",
            inputs=[Port(name="x", type=PortType.text)],
            outputs=[Port(name="x", type=PortType.text)],
            executor=Executor(kind=ExecutorKind.builtin, ref="a.b"),
        )


def test_param_name_overlap_with_input():
    with pytest.raises(ValueError):
        StepManifest(
            id="bad", name="参数重名", category="工具",
            inputs=[Port(name="x", type=PortType.text)],
            params=[Port(name="x", type=PortType.int)],
            executor=Executor(kind=ExecutorKind.builtin, ref="a.b"),
        )


def test_builtin_without_ref():
    with pytest.raises(ValueError):
        StepManifest(
            id="bad", name="无ref", category="工具",
            executor=Executor(kind=ExecutorKind.builtin),
        )


def test_action_chain_without_actions():
    with pytest.raises(ValueError):
        StepManifest(
            id="bad", name="无actions", category="工具",
            executor=Executor(kind=ExecutorKind.action_chain),
        )


# ── serialization round-trip ──

def test_roundtrip_yaml(tmp_path: Path):
    m = _make_builtin_step()
    path = tmp_path / "manifest.yaml"
    dump_manifest(m, path)
    assert path.exists()
    m2 = load_manifest(path)
    assert m2.id == m.id
    assert m2.name == m.name
    assert m2.executor.ref == "some.module.func"
    assert len(m2.inputs) == 1


def test_dump_to_dict():
    m = _make_builtin_step()
    d = dump_to_dict(m)
    assert isinstance(d, dict)
    assert d["id"] == "test_step"
    # should be JSON-serializable
    json.dumps(d)


# ── tags / fixtures ──

def test_tags_and_fixtures():
    m = StepManifest(
        id="tagged", name="带标签", category="审核",
        executor=Executor(kind=ExecutorKind.builtin, ref="x.y"),
        tags=["实验", "beta"],
        fixtures={"mock_response": "hello"},
        provenance="user",
    )
    assert m.tags == ["实验", "beta"]
    assert m.fixtures["mock_response"] == "hello"
    assert m.provenance == "user"
