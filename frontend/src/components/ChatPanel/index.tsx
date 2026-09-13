import { useEffect, useRef, useState } from 'react';
import { Avatar, Button, Empty, Input, Tag, Typography } from 'antd';
import {
  RobotOutlined,
  SendOutlined,
  UserOutlined,
  FileTextOutlined,
} from '@ant-design/icons';
import type { ChatMessage, TurnSnapshot } from '@/types';
import { StatusTag } from '@/components/StatusTag';
import { QuestionCard } from './QuestionCard';
import { ChatHistory } from './ChatHistory';

const { Text, Paragraph } = Typography;

interface Props {
  messages: ChatMessage[];
  history: TurnSnapshot[];
  viewingTurn: number | null;
  onView: (turn: number | null) => void;
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

export function ChatPanel({ messages, history, viewingTurn, onView, loading, hasQuestion, onAnswer, onSubmit, onViewExplanation }: Props) {
  const [draft, setDraft] = useState('');
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight,
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
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
      <div className="chat-panel__heading"><RobotOutlined /><strong>与 CasePath 对话</strong><span>从你的实际情况开始</span></div>
      <div className="chat-panel__list" ref={listRef} role="region" aria-label="对话记录" tabIndex={0}>
        <ChatHistory history={history} viewingTurn={viewingTurn} onView={onView} />
        {messages.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <span>
                描述你的民事问题，或点击右上角
                <Text strong>「预置问题」</Text>开始体验
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
          aria-label={pendingQuestion ? '补充事实' : '输入你的问题'}
          value={draft}
          autoSize={{ minRows: 1, maxRows: 4 }}
          placeholder={
            pendingQuestion
              ? '补充你知道的情况，不确定也可以直接说'
              : '描述你的民事问题，例如：我在健身房办了卡，已经付款……'
          }
          disabled={loading}
          onChange={(e) => setDraft(e.target.value)}
          onPressEnter={(e) => {
            if (!e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) {
              e.preventDefault();
              handleSend();
            }
          }}
        />
        <Button
          type="primary"
          icon={<SendOutlined />}
          disabled={loading || !draft.trim()}
          onClick={handleSend}
        >
          发送
        </Button>
      </div>
      <div className="chat-panel__hint">Enter 发送 · Shift + Enter 换行 · 请勿填写敏感个人信息</div>
    </div>
  );
}
