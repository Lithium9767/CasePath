"""当前P1 v1.3规则数据对应的固定官方比对与规则复核快照。

重新构建只能复制本记录，不得自动生成新复核或刷新已审核哈希。发布内容变化后，必须重新
执行官方全文比对和规则语义复核，才能有意识地更新本快照。
"""

from __future__ import annotations

from datetime import date

from casepath.contracts import ProvisionRecord
from casepath.ingestion.laws.civil_code import (
    EXPECTED_SOURCE_SHA256,
    EXPECTED_STATS_SHA256,
    EXPECTED_UPSTREAM_REVISION,
)
from casepath.ingestion.laws.jsonl import sha256_text
from casepath.ingestion.laws.manifest import AuthorityVerification
from casepath.rule_layer.ids import (
    RULE_DELAY_AFTER_DEMAND,
    RULE_GOOD_FAITH,
    RULE_NONPERFORMANCE_TERMINATION,
    RULE_SERVICE_TERMINATION_REFUND,
    RULE_TERMINATION_RESTITUTION,
)

REVIEWED_ON = date(2026, 9, 6)
EXPECTED_NORMALIZED_ARTICLE_HASHES = {
    509: "6092893c19fc13285fba96971bc601984509b92c926f45e1275e61e0fa2f12af",
    563: "6f8865de12e8358f28a6530d1d76bc1809ea1f6583a4976cd71816c6f67047e9",
    565: "52b64f120319276e2a3d437f5d73e6e67383c1eefcf2821f7fe43aa1c54e2c5c",
    566: "256a6da55401e696895bc77b22ab5fee4c7ecf253507f8c2d07d12be21e41dc3",
}
EXPECTED_NORMALIZED_CORPUS_SHA256 = (
    "a0cf6fecd892eb3d41155c093c86383106f383ba6e3a77d4cfdbd655e960605e"
)
EXPECTED_CANONICAL_OUTPUT_HASHES = {
    "legal_sources.jsonl": "10fcb635f59e758b257e83971523e68cb4b1ea364374bb05ca261d2bfd41cc1e",
    "provisions.jsonl": "56dff29fcff3cc669c4a1cbefcf00e638f3a0a9549f000096553179a7d815250",
    "rules.jsonl": "66c180c4e12592082b3d6c58d4f065c44639006a4f4b58a359a03c6ae38d355f",
    "source_spans.jsonl": "786c1dd600b6a06bd9d13bfec2270a16ec64b513a786fdfd78e60f6518a32158",
}
OFFICIAL_DOCUMENT_SHA256 = {
    "https://www.court.gov.cn/zixun/xiangqing/233181.html": (
        "16fb30a132114600f32059367dae0d6dd274519aa154126eae0ef9600cf4074e"
    ),
    "https://www.stats.gov.cn/gk/tjfg/xgfxfg/202503/t20250312_1958939.html": (
        "161fec0a908e75e08e62e3bbbaec19b8d207ff8f777a09d4db0f1b9787a6472f"
    ),
}
EXPECTED_AUTHORITY_URLS = frozenset(OFFICIAL_DOCUMENT_SHA256)


def normalized_corpus_sha256(provisions: list[ProvisionRecord]) -> str:
    """Hash the ordered article numbers and Unicode-whitespace-free text."""

    return sha256_text(
        "\n".join(
            f"{int(provision.article_no)}\t{''.join(provision.text.split())}"
            for provision in sorted(provisions, key=lambda item: int(item.article_no))
        )
    )


def authority_verification_snapshot() -> AuthorityVerification:
    """Return a fresh copy of the fixed review; never derive it from a build."""

    return AuthorityVerification(
        review_id="review.civil_code.p1_v1_3.2026-09-06",
        method="official_text_comparison_and_rule_review",
        verified_on=REVIEWED_ON,
        compared_article_count=1260,
        normalized_corpus_sha256=EXPECTED_NORMALIZED_CORPUS_SHA256,
        checked_article_numbers=[509, 563, 565, 566],
        whitespace_normalized_sha256={
            str(number): digest for number, digest in EXPECTED_NORMALIZED_ARTICLE_HASHES.items()
        },
        source_urls=list(OFFICIAL_DOCUMENT_SHA256),
        source_document_sha256=dict(OFFICIAL_DOCUMENT_SHA256),
        reviewed_upstream_revision=EXPECTED_UPSTREAM_REVISION,
        reviewed_input_sha256={
            "民法典_法条.json": EXPECTED_SOURCE_SHA256,
            "民法典_统计.json": EXPECTED_STATS_SHA256,
        },
        reviewed_output_sha256=dict(EXPECTED_CANONICAL_OUTPUT_HASHES),
        rule_findings={
            RULE_GOOD_FAITH: (
                "第509条：依法成立的合同应按约全面履行并遵循诚信原则；条件、履行义务后果"
                "及其引用与原文一致，本规则不独立推导解除权或固定退款金额。"
            ),
            RULE_DELAY_AFTER_DEMAND: (
                "第563条第一款第三项及第565条：迟延履行主要债务、催告送达、合理期限届满"
                "仍未履行共同支持解除权；通知或诉讼程序与取得解除权区分，未表述为自动解除。"
            ),
            RULE_NONPERFORMANCE_TERMINATION: (
                "第563条第一款第四项及第565条：违约导致合同目的不能实现支持解除权；"
                "替代履行在v1.3中是用于判断合同目的是否仍能实现的DIAGNOSTIC事实，不能"
                "脱离核心条件被独立当作法定阻却事由；解除生效另遵循通知或诉讼程序。"
            ),
            RULE_TERMINATION_RESTITUTION: (
                "第566条：依法解除后，未履行部分终止履行，已履行部分依履行情况及合同性质"
                "请求恢复原状或其他补救；不把恢复原状、损失赔偿与全额退款机械等同。"
            ),
            RULE_SERVICE_TERMINATION_REFUND: (
                "保留L2：已按第563条第一款第四项的合同目的不能实现路径收紧综合描述，"
                "结合第509、565、566条提供服务合同解除与费用补救的演示路径；替代履行只"
                "作为DIAGNOSTIC比较条件；不穷尽其他解除事由，不覆盖第564条期限及预付式"
                "消费专项解释，不计算固定退款金额。"
            ),
        },
        note=(
            "2026-09-06重新逐条比对最高人民法院与国家统计局公布的民法典全文。两个官方页面"
            "均包含唯一且连续的第1至1260条；去除Unicode空白后，每条正文与本次发布数据"
            "一致。source_document_sha256记录取得的HTML原始字节摘要，normalized_corpus_sha256"
            "按条号升序对‘条号\\t去空白正文’用换行连接后计算UTF-8 SHA-256。另对第509、563、"
            "565、566条支撑的5条规则逐项检查条件组、CORE/DIAGNOSTIC角色、法律后果及"
            "来源跨度，4条维持L3，综合规则以声明的适用限制维持L2。本记录继承P2既有"
            "法律复核并记录P1 v1.3结构适配核对，不构成独立第三方法律意见。该记录仅适用"
            "于绑定的源版本和发布文件，重建日期变化不代表重新复核。"
        ),
    )
