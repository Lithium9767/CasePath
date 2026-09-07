import { useEffect, useRef, useState } from 'react';
import { Avatar, Button, Empty, Input, Space, Tag, Typography } from 'antd';
import {
  RobotOutlined,
  SendOutlined,
  UserOutlined,
  FileTextOutlined,
} from '@ant-design/icons';
import type { ChatMessage } from '@/types';
import { StatusTag } from '@/components/StatusTag';
import { QuestionCard } from './QuestionCard';

const { Text, Paragraph } = Typography;

interface Props {
  messages: ChatMessage[];
  loading: boolean;
  hasQuestion: boolean;
  onAnswer: (answer: string) => void;
  onSubmit: (query: string) => void;
  onViewExplanation: () => void;
}

const UnderstandingBubble = ({ msg }: { msg: Extract<ChatMessage, { kind: 'understanding' }> }) => {
  const { candidate_claims: claims, condition_states: states, condition_labels: labels } =
    msg.understanding;
  return (
    <div className="bubble bubble--assistant">
      <div className="bubble__label">
        <FileTextOutlined /> 系统当前理解
      </div>
      {claims.length > 0 && (
        <div className="bubble__claims">
          {claims.map((c, i) => (
            <Tag key={`${c.claim_type}-${i}`} color="blue">
              {c.claim_type}（{Math.round(c.confidence * 100)}%）
            </Tag>
          ))}
        </div>
      )}
      {states.length > 0 && (
        <div className="bubble__states">
          {states.map((s) => (
            <div key={s.condition_id} className="bubble__state">
              <span className="bubble__state-name">{labels[s.condition_id] ?? s.condition_id}</span>
              <StatusTag status={s.status} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

const ExplanationBubble = ({ msg }: { msg: Extract<ChatMessage, { kind: 'explanation' }> }) => (
  <div className="bubble bubble--assistant">
    <div className="bubble__label">
      <RobotOutlined /> 根据目前信息的说明
    </div>
    <Paragraph className="bubble__text">{msg.explanation.main_explanation}</Paragraph>
    <Text type="secondary" style={{ fontSize: 12 }}>
      详细的条件、案例与原文证据见右侧面板。
    </Text>
  </div>
);

export function ChatPanel({ messages, loading, hasQuestion, onAnswer, onSubmit, onViewExplanation }: Props) {
  const [draft, setDraft] = useState('');
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages, loading]);

  const lastMsg = messages[messages.length - 1];
  const pendingQuestion =
    hasQuestion && lastMsg?.role === 'assistant' && lastMsg.kind === 'question'
      ? lastMsg.question
      : null;

  const handleSend = () => {
    const v = draft.trim();
    if (!v || loading) return;
    if (pendingQuestion) {
      onAnswer(v);
    } else {
      onSubmit(v);
    }
    setDraft('');
  };

  return (
    <div className="chat-panel">
      <div className="chat-panel__list" ref={listRef}>
        {messages.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <span>
                描述你的民事问题，或点击右上角
                <Text strong>「预置演示」</Text>查看完整流程
              </span>
            }
            style={{ marginTop: 80 }}
          />
        ) : (
          messages.map((msg) => {
            if (msg.role === 'user') {
              return (
                <div key={msg.id} className="chat-row chat-row--user">
                  <Avatar icon={<UserOutlined />} className="chat-avatar chat-avatar--user" />
                  <div className="bubble bubble--user">{msg.text}</div>
                </div>
              );
            }
            if (msg.kind === 'understanding') {
              return (
                <div key={msg.id} className="chat-row chat-row--assistant">
                  <Avatar icon={<RobotOutlined />} className="chat-avatar" />
                  <UnderstandingBubble msg={msg} />
                </div>
              );
            }
            if (msg.kind === 'explanation') {
              return (
                <div key={msg.id} className="chat-row chat-row--assistant">
                  <Avatar icon={<RobotOutlined />} className="chat-avatar" />
                  <ExplanationBubble msg={msg} />
                </div>
              );
            }
            if (msg.kind === 'text') {
              return (
                <div key={msg.id} className="chat-row chat-row--assistant">
                  <Avatar icon={<RobotOutlined />} className="chat-avatar" />
                  <div className="bubble bubble--assistant">{msg.text}</div>
                </div>
              );
            }
            return (
              <div key={msg.id} className="chat-row chat-row--assistant">
                <Avatar icon={<RobotOutlined />} className="chat-avatar" />
                <QuestionCard
                  question={msg.question}
                  disabled={loading || msg.id !== messages[messages.length - 1].id}
                  onAnswer={onAnswer}
                  onViewExplanation={onViewExplanation}
                />
              </div>
            );
          })
        )}
        {loading && (
          <div className="chat-row chat-row--assistant">
            <Avatar icon={<RobotOutlined />} className="chat-avatar" />
            <div className="bubble bubble--assistant bubble--typing">正在分析…</div>
          </div>
        )}
      </div>

      <div className="chat-panel__input">
        <Input.TextArea
          value={draft}
          autoSize={{ minRows: 1, maxRows: 4 }}
          placeholder={
            pendingQuestion
              ? '回答当前追问，可输入「不清楚」或否定/假设事实'
              : '描述你的民事问题，例如：我在健身房办了卡，已经付款……'
          }
          disabled={loading}
          onChange={(e) => setDraft(e.target.value)}
          onPressEnter={(e) => {
            if (!e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
        />
        <Button
          type="primary"
          icon={<SendOutlined />}
          disabled={loading}
          onClick={handleSend}
        >
          发送
        </Button>
      </div>
      <Space size={4} style={{ padding: '4px 12px 0' }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          回车发送，Shift+回车换行
        </Text>
      </Space>
    </div>
  );
}
