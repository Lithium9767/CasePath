import { Alert, Card, Collapse, List, Tag, Typography } from 'antd';
import { ApartmentOutlined, BranchesOutlined, FileProtectOutlined } from '@ant-design/icons';
import type { ResolvedSnapshot } from '@/types';
import { StatusTag } from '@/components/StatusTag';
import { conditionLabels } from '@/utils/contracts';

const { Text, Paragraph } = Typography;

function caseEvidenceAlert(plan: ResolvedSnapshot['explanation_plan']) {
  const roleCount =
    (plan.role_assigned_case_count ??
      plan.support_case_ids.length + plan.limiting_case_ids.length + plan.boundary_case_ids.length) ||
    0;
  const undetermined = plan.undetermined_case_count ?? 0;
  const fused = plan.fused_case_count ?? 0;
  if (roleCount > 0) return null;
  if (undetermined > 0) {
    const reasons = (plan.undetermined_reason_summary ?? []).join('、') || '比较证据不足';
    return {
      message: '检索到候选，但比较证据不足',
      description: `有 ${undetermined} 件候选案例尚未确定支持/限制角色（${reasons}）。这不等于没有案例。`,
    };
  }
  if (plan.applicable_rule_ids.length > 0 && fused === 0) {
    return {
      message: '暂无相关案例候选',
      description: '已检索到候选规则，但本轮没有进入融合池的可比对案例。这不等于没有相关法律依据。',
    };
  }
  if (plan.applicable_rule_ids.length > 0) {
    return {
      message: '案例角色尚未确定',
      description: '已有规则参考，但尚无已定角色的支持/限制案例。',
    };
  }
  return null;
}

function stopReasonAlert(plan: ResolvedSnapshot['explanation_plan']) {
  switch (plan.question_stop_reason) {
    case 'QUESTION_BUDGET_REACHED':
      return {
        message: '追问预算已用尽',
        description: '本轮模型调用预算已耗尽，解释基于当前已收集事实，不代表信息充分。',
      };
    case 'USER_UNABLE_TO_ANSWER':
      return {
        message: '部分事实暂无法补充',
        description: '你已表示不清楚或无法回答部分问题，相关条件将保持未知。',
      };
    case 'USER_DECLINED':
      return {
        message: '部分问题已跳过',
        description: '你已表示暂不回答部分问题，相关条件将保持未知。',
      };
    case 'ALL_ALREADY_ANSWERED':
      return {
        message: '没有新的关键事实需要追问',
        description: '已说明的事实已覆盖当前候选；这不等于证据已经充分。',
      };
    case 'NO_KEY_MISSING_FACT':
      return {
        message: '未识别到可追问的关键缺失事实',
        description: '当前轮没有适合继续追问的事实点。',
      };
    case 'GENERATION_FAILED':
      return {
        message: '追问生成失败',
        description: '问题生成未能完成；已保留当前检索结果，不能据此认为无需追问。',
      };
    default:
      return null;
  }
}

export function ExplanationPanel({ snapshot }: { snapshot: ResolvedSnapshot }) {
  const plan = snapshot.explanation_plan;
  const labels = conditionLabels(snapshot);
  const userStates = new Map(
    snapshot.query_state.condition_states.map((s) => [s.condition_id, s.status]),
  );
  const caseAlert = caseEvidenceAlert(plan);
  const stopAlert = stopReasonAlert(plan);

  return (
    <Card
      size="small"
      title="最终法律解释"
      className="panel-card panel-card--explanation"
      extra={<Tag color="blue">可溯源解释</Tag>}
    >
      <Paragraph className="explanation-main">{plan.main_explanation}</Paragraph>
      {caseAlert && (
        <Alert
          type="warning"
          showIcon
          message={caseAlert.message}
          description={caseAlert.description}
          style={{ marginBottom: 12 }}
        />
      )}
      {stopAlert && (
        <Alert
          type="info"
          showIcon
          message={stopAlert.message}
          description={stopAlert.description}
          style={{ marginBottom: 12 }}
        />
      )}

      <Collapse
        size="small"
        ghost
        defaultActiveKey={['rules', 'states']}
        items={[
          {
            key: 'rules',
            label: (
              <span>
                <ApartmentOutlined /> 当前参考规则
              </span>
            ),
            children: (
              <div>
                {plan.applicable_rule_ids.length === 0 ? (
                  <Text type="secondary">本轮暂无稳定参考规则</Text>
                ) : (
                  plan.applicable_rule_ids.map((rid) => {
                    const rule = snapshot.rules[rid];
                    if (!rule) return <div key={rid}>`{rid}`（详情未解析）</div>;
                    return (
                      <div key={rid} className="explanation-rule">
                        <Text strong>{rule.title}</Text>
                        <div className="explanation-rule__provisions">
                          {rule.provisions.map((p) => (
                            <Tag key={p.provision_id} color="geekblue">
                              {p.article_no}
                            </Tag>
                          ))}
                          <Tag color={rule.maturity === 'L3' ? 'green' : 'default'}>
                            成熟度 {rule.maturity}
                          </Tag>
                          <Tag>参考用途</Tag>
                        </div>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {rule.claim_types.join('、')}
                        </Text>
                      </div>
                    );
                  })
                )}
              </div>
            ),
          },
          {
            key: 'states',
            label: (
              <span>
                <BranchesOutlined /> 条件状态
              </span>
            ),
            children: (
              <div className="state-list">
                {snapshot.query_state.condition_states.map((s) => (
                  <div key={s.condition_id} className="state-row">
                    <span className="state-row__name">{labels[s.condition_id] ?? s.condition_id}</span>
                    <StatusTag status={s.status} />
                  </div>
                ))}
              </div>
            ),
          },
          {
            key: 'branches',
            label: (
              <span>
                <FileProtectOutlined /> 条件化分支（{plan.conditional_branches.length}）
              </span>
            ),
            children:
              plan.conditional_branches.length > 0 ? (
                <List
                  size="small"
                  dataSource={plan.conditional_branches}
                  renderItem={(b) => (
                    <List.Item>
                      <List.Item.Meta title={b.condition} description={b.explanation} />
                    </List.Item>
                  )}
                />
              ) : (
                <Text type="secondary">无</Text>
              ),
          },
          {
            key: 'unresolved',
            label: (
              <span>
                <FileProtectOutlined /> 未解决条件（{plan.unresolved_condition_ids.length}）
              </span>
            ),
            children:
              plan.unresolved_condition_ids.length > 0 ? (
                <div className="state-list">
                  {plan.unresolved_condition_ids.map((cid) => (
                    <div key={cid} className="state-row">
                      <span className="state-row__name">{labels[cid] ?? cid}</span>
                      <StatusTag status={userStates.get(cid) ?? 'UNKNOWN'} />
                    </div>
                  ))}
                </div>
              ) : (
                <Text type="secondary">无</Text>
              ),
          },
        ]}
      />

      {plan.candidate_claims.length > 0 && (
        <div className="panel-section" style={{ marginTop: 12 }}>
          <div className="panel-section__title">候选请求权</div>
          {plan.candidate_claims.map((c, i) => (
            <Tag key={`${c}-${i}`} color="blue">
              {c}
            </Tag>
          ))}
        </div>
      )}

      <Alert
        type="info"
        showIcon
        message="非法律意见"
        description={plan.disclaimer}
        style={{ marginTop: 12 }}
      />
    </Card>
  );
}
