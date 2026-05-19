"""Preset action executors — composable steps for custom pipeline phases."""

from generator.c_pipeline.actions.base import ActionContext, ActionResult, BaseAction
from generator.c_pipeline.actions.runner import ActionRunner

__all__ = ["ActionContext", "ActionResult", "ActionRunner", "BaseAction"]
