import { Card, Tooltip } from 'antd';
import type { QuestionCandidate } from '@/types';
import { formatScore } from '@/utils/status';

export function QuestionUtilityPanel({
  question,
  labels,
}: {
  question: QuestionCandidate;
  labels: Record<string, string>;
}) {
  const components = question.score_components;
  return (
    <Card
      size="small"
      title="高价值追问评分"
      extra={
        <Tooltip title="Utility(c) = α·规则中心性 + β·案例分化 + γ·可回答性 + δ·可取证性 − 交互成本">
          <span className="panel-card__hint">Utility 计算</span>
        </Tooltip>
      }
      className="panel-card"
    >
      <div className="utility-head">
        <span className="utility-head__name">
          {labels[question.condition_id] ?? question.condition_id}
        </span>
        <span className="utility-head__total">{formatScore(question.utility)}</span>
      </div>
      <div className="utility-list">
        {components.map((c) => (
          <div key={c.name} className="utility-comp-row">
            <span className="utility-comp-row__name">{c.name}</span>
            <span
              className="utility-comp-row__value"
              style={{ color: c.value >= 0 ? '#52c41a' : '#ff4d4f' }}
            >
              {c.value >= 0 ? '+' : ''}
              {formatScore(c.value)}
            </span>
            <span className="utility-comp-row__explain">{c.explanation}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}
