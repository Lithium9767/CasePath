"""P4 阶段诊断：只观测，不修改算法状态。"""

from casepath.adapters.diagnostics.stage_events import emit_stage_event

__all__ = ["emit_stage_event"]
