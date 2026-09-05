"""会话服务的增量合同；不修改已经冻结的十一种 v1.1 对象。"""

from typing import Literal

from pydantic import Field, field_validator, model_validator

from .base import ContractModel
from .query import QueryConditionState, UserFact


class CreateSessionRequest(ContractModel):
    """前端只提交问题，会话编号由服务端生成。"""

    contract_version: Literal["1.2"] = "1.2"
    query: str = Field(min_length=1)

    @field_validator("query")
    @classmethod
    def reject_blank_query(cls, value: str) -> str:
        # 只检查空白，不改写用户原文，便于后续定位证据。
        if not value.strip():
            raise ValueError("query must not be blank")
        return value


class AnswerInterpretation(ContractModel):
    """P4 返回事实和条件更新，不得替换整个会话或生成最终法律判断。"""

    contract_version: Literal["1.2"] = "1.2"
    new_facts: list[UserFact] = Field(default_factory=list)
    condition_updates: list[QueryConditionState] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "AnswerInterpretation":
        # 跨对象引用由会话服务在合并历史事实后校验，此处只检查本批次重复。
        for identifiers in (
            [item.fact_id for item in self.new_facts],
            [item.condition_id for item in self.condition_updates],
        ):
            if len(identifiers) != len(set(identifiers)):
                raise ValueError("interpretation IDs must be unique")
        return self
