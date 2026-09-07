import { Alert, Card, Collapse, List, Tag, Typography } from 'antd';
import { ApartmentOutlined, BranchesOutlined, FileProtectOutlined } from '@ant-design/icons';
import type { ResolvedSnapshot } from '@/types';
import { StatusTag } from '@/components/StatusTag';
import { conditionLabels } from '@/utils/contracts';

const { Text, Paragraph } = Typography;

export function ExplanationPanel({ snapshot }: { snapshot: ResolvedSnapshot }) {
  const plan = snapshot.explanation_plan;
  const labels = conditionLabels(snapshot);
  const userStates = new Map(
    snapshot.query_state.condition_states.map((s) => [s.condition_id, s.status]),
  );

  return (
    <Card
      size="small"
      title="最终法律解释"
      className="panel-card panel-card--explanation"
      extra={<Tag color="blue">可溯源解释</Tag>}
    >
      <Paragraph className="explanation-main">{plan.main_explanation}</Paragraph>
      {plan.applicable_rule_ids.length > 0 &&
        plan.support_case_ids.length + plan.limiting_case_ids.length + plan.boundary_case_ids.length ===
          0 && (
          <Alert
            type="warning"
            showIcon
            message="案例证据不足"
            description="已检索到候选规则，但缺少足够的可比对案例。这不等于没有相关法律依据。"
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
                <ApartmentOutlined /> 适用规则
              </span>
            ),
            children: (
              <div>
                {plan.applicable_rule_ids.map((rid) => {
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
                      </div>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {rule.claim_types.join('、')}
                      </Text>
                    </div>
                  );
                })}
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
