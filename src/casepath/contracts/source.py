from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel, Identifier, SourceSpan


class LegalSourceRecord(ContractModel): # 一整部法律、司法解释或其他规范性法律来源
    contract_version: Literal["1.1"] = "1.1" # 法律来源合同版本号，只接受1.1
    source_id: Identifier # 法律来源稳定唯一编号，例如law.prc.civil_code.2021
    title: str = Field(min_length=1) # 法律来源正式名称
    source_type: str = Field(min_length=1) # 来源类型，例如LAW或JUDICIAL_INTERPRETATION
    authority: str | None = None # 制定或发布机关，例如全国人民代表大会
    jurisdiction: str | None = None # 适用法域，例如中华人民共和国
    effective_from: date | None = None # 开始生效日期
    effective_to: date | None = None # 失效日期；仍然有效时为空
    official_url: str | None = None # 官方公开来源地址

    @model_validator(mode="after")
    def validate_effective_dates(self) -> LegalSourceRecord:
        # 同时存在起止日期时，失效日期不能早于生效日期。
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to < self.effective_from
        ):
            raise ValueError("effective_to must be greater than or equal to effective_from")
        return self


class ProvisionRecord(ContractModel): # 一条完整法条原文，是规则层进行检索和引用的基础数据
    contract_version: Literal["1.1"] = "1.1" # 法条合同版本号，只接受1.1
    provision_id: Identifier # 法条稳定唯一编号，例如law.prc.civil_code.2021.article_0563
    source_id: Identifier # 该法条所属的LegalSourceRecord.source_id
    article_no: str = Field(min_length=1) # 人类可读条号，例如“第五百六十三条”
    title: str = Field(min_length=1) # 法条显示名称
    text: str = Field(min_length=1) # 经核验的完整法条正文
    effective_from: date | None = None # 该版本法条开始生效日期
    effective_to: date | None = None # 该版本法条失效日期；仍然有效时为空
    source_spans: list[SourceSpan] = Field(min_length=1) # 法条正文对应的原文片段，至少一处

    @model_validator(mode="after")
    def validate_source_and_dates(self) -> ProvisionRecord:
        # 法条原文片段必须属于当前法律来源，避免跨文档引用。
        foreign_sources = {
            span.source_id for span in self.source_spans if span.source_id != self.source_id
        }
        if foreign_sources:
            raise ValueError(f"source spans reference other sources: {sorted(foreign_sources)}")

        # 同时存在起止日期时，失效日期不能早于生效日期。
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to < self.effective_from
        ):
            raise ValueError("effective_to must be greater than or equal to effective_from")
        return self
