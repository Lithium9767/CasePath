import { Empty, Steps } from 'antd';
import type { ResolvedSnapshot } from '@/types';
import { conditionLabels } from '@/utils/contracts';
import { UnderstandingPanel } from './UnderstandingPanel';
import { ProjectionMatrix } from './ProjectionMatrix';
import { ComparisonPanel } from './ComparisonPanel';
import { QuestionUtilityPanel } from './QuestionUtilityPanel';
import { CasePanel } from './CasePanel';
import { ExplanationPanel } from './ExplanationPanel';
import { EvidencePanel } from './EvidencePanel';

interface Props {
  snapshot: ResolvedSnapshot | null;
}

const FLOW_STEPS = [
  { title: '识别请求权' },
  { title: '检索规则' },
  { title: '条件投影' },
  { title: '正反案例对比' },
  { title: '高价值追问' },
  { title: '可溯源解释' },
];

const EmptyOverview = () => (
  <div className="right-empty">
    <div className="right-empty__title">CasePath 分析流程</div>
    <Steps direction="vertical" size="small" current={-1} items={FLOW_STEPS} />
    <Empty
      image={Empty.PRESENTED_IMAGE_SIMPLE}
      description="输入问题后，此处将实时展示系统理解、条件投影矩阵、追问价值与最终解释"
      style={{ marginTop: 24 }}
    />
  </div>
);

export function RightPanel({ snapshot }: Props) {
  if (!snapshot) {
    return (
      <div className="right-panel">
        <EmptyOverview />
      </div>
    );
  }

  const explaining = !snapshot.next_question;

  return (
    <div className="right-panel">
      <UnderstandingPanel snapshot={snapshot} />
      <ProjectionMatrix snapshot={snapshot} />
      {snapshot.comparison_bundle && <ComparisonPanel snapshot={snapshot} />}
      {snapshot.next_question && (
        <QuestionUtilityPanel question={snapshot.next_question} labels={conditionLabels(snapshot)} />
      )}
      <CasePanel snapshot={snapshot} />
      {explaining && <ExplanationPanel snapshot={snapshot} />}
      {explaining && <EvidencePanel snapshot={snapshot} />}
    </div>
  );
}
