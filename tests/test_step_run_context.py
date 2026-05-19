"""Phase 3.1.3: StepRunContext unit tests."""
from generator.c_pipeline.actions.base import StepRunContext


def test_basic_binding():
    ctx = StepRunContext()
    ctx.write_step_output("paste", {"article": "hello world"})
    result = ctx.bind("{steps.paste.article}")
    assert result == "hello world"


def test_binding_not_found_returns_empty():
    ctx = StepRunContext()
    result = ctx.bind("{steps.missing.port}")
    assert result == ""


def test_params_binding():
    ctx = StepRunContext(params={"dry_run": True, "max_tokens": 1000})
    # simple scalar returns raw value (bool/int)
    assert ctx.bind("{params.dry_run}") is True
    assert ctx.bind("{params.max_tokens}") == 1000
    # in a template string, values are stringified
    assert "True" in ctx.bind("dry={params.dry_run}")
    assert "1000" in ctx.bind("tok={params.max_tokens}")


def test_globals_binding():
    ctx = StepRunContext(globals={"work_dir": "/tmp/test"})
    assert ctx.bind("{globals.work_dir}") == "/tmp/test"


def test_default_fallback():
    ctx = StepRunContext()
    result = ctx.bind("{steps.x.y | default('未找到')}")
    assert result == "未找到"


def test_mixed_template():
    ctx = StepRunContext(globals={"work_dir": "/tmp"}, params={"name": "测试"})
    ctx.write_step_output("s1", {"out": "result"})
    result = ctx.bind("文件在 {globals.work_dir}，名称 {params.name}，数据 {steps.s1.out}")
    assert "result" in result
    assert "测试" in result
    assert "/tmp" in result


def test_simple_scalar_binding():
    ctx = StepRunContext(params={"count": 42})
    result = ctx.bind("{params.count}")
    assert result == 42  # raw int


def test_last_step_output_compat():
    ctx = StepRunContext()
    ctx.write_step_output("s1", {"content": "很长的文本内容" * 10})
    assert len(ctx._last_step_output) > 0


def test_missing_step_returns_empty():
    ctx = StepRunContext()
    assert ctx.bind("{steps.nonexistent}") == ""


def test_none_value_fallback():
    ctx = StepRunContext()
    result = ctx.bind("{steps.x.y | default('默认')}")
    assert result == "默认"
