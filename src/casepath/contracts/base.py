#它主要是帮助类型注解工作的，不参与核心业务逻辑。
# 解析在 class SourceSpan 中的 def validate_offsets(self) -> SourceSpan:
from __future__ import annotations

# 在一个基础类型上，再附加一些额外规则。
# 它必须是字符串，而且不能为空，而且自动去掉前后空格。
# 就可以用：
# Annotated[
#     str,
#     StringConstraints(strip_whitespace=True, min_length=1),
# ]
# 来表示。
from typing import Annotated

# Pydantic 是一个 Python 数据校验库。
# 帮你规定“数据应该长什么样”，并自动检查数据对不对。
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# 自动处理首尾空格，最小长度为1
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# 数值必须在0到1之间
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class ContractModel(BaseModel):
    """Base class for all values exchanged across team-owned modules."""
    # 继承 BaseModel
    # model_validate()：把外部数据检查后变成模型对象
    # model_dump()：把模型对象变回普通 Python 数据
    # model_json_schema()：把模型的“结构规则”导出成 JSON Schema

    # 禁止额外的未定义字段，当修改赋值时再次进行校验
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# 定义 pydantic数据模型
# 原文证据片段
class SourceSpan(ContractModel):
    span_id: Identifier # 证据原文一段文字编号
    source_id: Identifier # 证据原文所属文档编号
    section: str | None = None # 证据原文所属章节
    paragraph_id: str | None = None # 证据原文所属段落编号
    start_offset: int = Field(ge=0) # 证据原文起始位置
    end_offset: int = Field(ge=0)  # 证据原文结束位置
    quote: str = Field(min_length=1) # 证据原文内容

    # 等所有字段完成类型检查以后，再检查这些字段之间的关系。
    # 检查结束位置是否大于起始位置
    @model_validator(mode="after")
    def validate_offsets(self) -> SourceSpan:
        if self.end_offset < self.start_offset:
            raise ValueError("end_offset must be greater than or equal to start_offset")
        return self

# 表示某个总分中的一个评分因素。
class ScoreComponent(ContractModel):
    name: Identifier # 评分项的稳定名称。
    value: float # 评分项的得分。
    explanation: str # 评分项的解释。
