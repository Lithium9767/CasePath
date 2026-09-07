import { useState } from 'react';
import { Alert, Button, Space, Tag, Typography } from 'antd';
import { ExperimentOutlined, ReloadOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { ChatPanel } from '@/components/ChatPanel';
import { RightPanel } from '@/components/RightPanel';
import { useSession } from '@/hooks/useSession';
import { PRESET_QUERY } from '@/mock/scenario';
import { SESSION_COLOR, SESSION_LABEL } from '@/utils/status';
import type { CapabilityStatus, SessionStatus } from '@/types';

const { Title, Text } = Typography;

const CAP_LABEL: Record<string, string> = {
  llm: 'LLM',
  legal_graph: 'Neo4j',
  condition_projector: '条件投影',
  rule_retriever: '规则召回',
  case_retriever: '案例召回',
  session_repository: '会话',
};

function capColor(item: CapabilityStatus): string {
  if (!item.available) return 'default';
  if (item.degraded) return 'gold';
  if (item.mode === 'LIVE') return 'green';
  return 'blue';
}

export default function App() {
  const session = useSession();
  const [demoing, setDemoing] = useState(false);

  const status: SessionStatus = session.snapshot?.query_state.status ?? 'INITIAL';
  const shownCaps = session.capabilities.filter((item) => CAP_LABEL[item.capability]);

  const runDemo = async () => {
    setDemoing(true);
    try {
      session.reset();
      await new Promise((r) => setTimeout(r, 300));
      await session.submitQuery(PRESET_QUERY);
    } finally {
      setDemoing(false);
    }
  };

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-header__brand">
          <div className="app-header__logo">CasePath</div>
          <div className="app-header__meta">
            <Title level={5} style={{ margin: 0 }}>
              规则—案例对比驱动的交互式民事法律解释
            </Title>
            <Text type="secondary" style={{ fontSize: 12 }}>
              正式会话接口 · 条件投影 · 案例证据 · 高价值追问
            </Text>
          </div>
        </div>
        <Space size={8} wrap>
          <Tag color={SESSION_COLOR[status]}>{SESSION_LABEL[status]}</Tag>
          <Button
            icon={<ExperimentOutlined />}
            onClick={runDemo}
            loading={demoing}
            disabled={session.loading && !demoing}
          >
            预置问题
          </Button>
          <Button icon={<ReloadOutlined />} onClick={session.reset} disabled={demoing}>
            重置
          </Button>
        </Space>
      </header>

      {shownCaps.length > 0 && (
        <div className="app-capabilities">
          {shownCaps.map((item) => (
            <Tag key={item.capability} color={capColor(item)} title={item.reason ?? ''}>
              {CAP_LABEL[item.capability]} · {item.mode}
              {item.degraded ? ' · 降级' : ''}
              {!item.available ? ' · 不可用' : ''}
            </Tag>
          ))}
        </div>
      )}

      {session.error && (
        <Alert
          type="error"
          showIcon
          closable
          message="请求未成功，已保留当前会话"
          description={session.error}
          style={{ margin: '0 16px 8px' }}
        />
      )}

      <div className="app-body">
        <aside className="app-left">
          <ChatPanel
            messages={session.messages}
            loading={session.loading}
            hasQuestion={!!session.snapshot?.next_question}
            onAnswer={session.answerQuestion}
            onSubmit={session.submitQuery}
            onViewExplanation={session.viewExplanationNow}
          />
        </aside>
        <section className="app-right">
          <RightPanel snapshot={session.snapshot} />
        </section>
      </div>

      <footer className="app-footer">
        <SafetyCertificateOutlined /> 本系统输出仅供学习参考，不构成正式法律意见
      </footer>
    </div>
  );
}
