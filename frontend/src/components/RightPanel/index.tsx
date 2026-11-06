import { Alert, Button, Empty, Steps } from 'antd';
import type { ResolvedSnapshot, TurnSnapshot } from '@/types';
import { conditionLabels } from '@/utils/contracts';
import { UnderstandingPanel } from './UnderstandingPanel';
import { KnowledgeGraphPanel } from './KnowledgeGraphPanel';
import { LiveRetrievalGraph } from './LiveRetrievalGraph';
import { ProjectionMatrix } from './ProjectionMatrix';
import { ComparisonPanel } from './ComparisonPanel';
import { QuestionUtilityPanel } from './QuestionUtilityPanel';
import { CasePanel } from './CasePanel';
import { ExplanationPanel } from './ExplanationPanel';
import { EvidencePanel } from './EvidencePanel';

interface Props {
  snapshot: ResolvedSnapshot | null;
  history: TurnSnapshot[];
  viewingTurn: number | null;
  onViewTurn: (turn: number | null) => void;
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
      description="输入问题后，此处将实时展示系统理解、知识图谱、条件投影矩阵、追问价值与最终解释"
      style={{ marginTop: 24 }}
    />
  </div>
);

export function RightPanel({ snapshot, history, viewingTurn, onViewTurn }: Props) {
  if (!snapshot) {
    return (
      <div className="right-panel">
        <EmptyOverview />
        {import.meta.env.VITE_USE_MOCK !== 'true' && <LiveRetrievalGraph snapshot={null} />}
      </div>
    );
  }

  const viewed =
    viewingTurn !== null ? history.find((h) => h.turn === viewingTurn)?.snapshot : undefined;
  const effective = viewed ?? snapshot;
  const explaining = !effective.next_question;

  return (
    <div className="right-panel">
      {viewingTurn !== null && (
        <Alert
          type="info"
          showIcon
          message={`正在回看第 ${viewingTurn} 轮的状态`}
          action={
            <Button size="small" onClick={() => onViewTurn(null)}>
              返回最新
            </Button>
          }
        />
      )}
      <UnderstandingPanel snapshot={effective} />
      <KnowledgeGraphPanel snapshot={effective} />
      <ProjectionMatrix snapshot={effective} />
      {effective.comparison_bundle && <ComparisonPanel snapshot={effective} />}
      {effective.next_question && (
        <QuestionUtilityPanel question={effective.next_question} labels={conditionLabels(effective)} />
      )}
      <CasePanel snapshot={effective} />
      {explaining && <ExplanationPanel snapshot={effective} />}
      {explaining && <EvidencePanel snapshot={effective} />}
    </div>
  );
}
