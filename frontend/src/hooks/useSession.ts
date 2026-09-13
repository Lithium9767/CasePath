import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, api } from '@/api/client';
import type { CapabilityStatus, ChatMessage, ResolvedSnapshot, TurnSnapshot, Understanding } from '@/types';
import { conditionLabels } from '@/utils/contracts';

let idCounter = 0;
const nextId = () => `msg-${Date.now()}-${idCounter++}`;

export interface SessionController {
  snapshot: ResolvedSnapshot | null;
  messages: ChatMessage[];
  loading: boolean;
  error: string | null;
  capabilities: CapabilityStatus[];
  history: TurnSnapshot[];
  viewingTurn: number | null;
  setViewingTurn: (turn: number | null) => void;
  submitQuery: (query: string) => Promise<void>;
  answerQuestion: (answer: string) => Promise<void>;
  viewExplanationNow: () => Promise<void>;
  reset: () => void;
}

const understandingOf = (s: ResolvedSnapshot): Understanding => ({
  candidate_claims: s.query_state.candidate_claims,
  condition_states: s.query_state.condition_states,
  condition_labels: conditionLabels(s),
});

const isExplaining = (s: ResolvedSnapshot): boolean =>
  s.next_question == null &&
  (s.query_state.status === 'READY_TO_EXPLAIN' ||
    s.query_state.status === 'COMPLETED' ||
    s.query_state.status === 'DEGRADED' ||
    Boolean(s.explanation_plan.main_explanation));

function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    const hint = err.reason ? `（${err.reason}）` : '';
    return `${err.message}${hint}`;
  }
  return err instanceof Error ? err.message : '请求失败';
}

export function useSession(): SessionController {
  const [snapshot, setSnapshot] = useState<ResolvedSnapshot | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<CapabilityStatus[]>([]);
  const [history, setHistory] = useState<TurnSnapshot[]>([]);
  const [viewingTurn, setViewingTurn] = useState<number | null>(null);
  const snapshotRef = useRef<ResolvedSnapshot | null>(null);

  const setBoth = useCallback((s: ResolvedSnapshot | null) => {
    snapshotRef.current = s;
    setSnapshot(s);
  }, []);

  useEffect(() => {
    api.capabilities().then(setCapabilities).catch(() => {
      setCapabilities([]);
    });
  }, []);

  const submitQuery = useCallback(
    async (query: string) => {
      const trimmed = query.trim();
      if (!trimmed) return;
      setLoading(true);
      setError(null);
      setMessages((prev) => [
        ...prev,
        { id: nextId(), role: 'user', text: trimmed, timestamp: Date.now() },
      ]);
      try {
        const res = await api.analyze(trimmed);
        setBoth(res);
        setHistory([{ turn: 0, snapshot: res }]);
        setViewingTurn(null);
        setMessages((prev) => {
          const next: ChatMessage[] = [
            ...prev,
            {
              id: nextId(),
              role: 'assistant',
              kind: 'understanding',
              understanding: understandingOf(res),
              timestamp: Date.now(),
            },
          ];
          if (res.next_question) {
            next.push({
              id: nextId(),
              role: 'assistant',
              kind: 'question',
              question: res.next_question,
              timestamp: Date.now(),
            });
          } else if (isExplaining(res)) {
            next.push({
              id: nextId(),
              role: 'assistant',
              kind: 'explanation',
              explanation: res.explanation_plan,
              timestamp: Date.now(),
            });
          }
          return next;
        });
      } catch (err) {
        const text = errorText(err);
        setError(text);
        setMessages((prev) => [
          ...prev,
          { id: nextId(), role: 'assistant', kind: 'text', text, timestamp: Date.now() },
        ]);
      } finally {
        setLoading(false);
      }
    },
    [setBoth],
  );

  const answerQuestion = useCallback(
    async (answer: string) => {
      const s = snapshotRef.current;
      if (!s?.next_question) return;
      const q = s.next_question;
      const before = s;
      setLoading(true);
      setError(null);
      setMessages((prev) => [
        ...prev,
        { id: nextId(), role: 'user', text: answer, timestamp: Date.now() },
      ]);
      try {
        const selected = q.options.includes(answer) ? answer : null;
        const res = await api.answer({
          session_id: s.query_state.session_id,
          contract_version: '1.1',
          question_id: q.question_id,
          condition_id: q.condition_id,
          answer,
          selected_option: selected,
        });
        setBoth(res);
        setHistory((prev) => [...prev, { turn: prev.length, snapshot: res }]);
        setViewingTurn(null);
        setMessages((prev) => {
          const next: ChatMessage[] = [...prev];
          if (res.next_question) {
            next.push({
              id: nextId(),
              role: 'assistant',
              kind: 'question',
              question: res.next_question,
              timestamp: Date.now(),
            });
          } else if (res.explanation_plan) {
            next.push({
              id: nextId(),
              role: 'assistant',
              kind: 'explanation',
              explanation: res.explanation_plan,
              timestamp: Date.now(),
            });
          }
          return next;
        });
      } catch (err) {
        const text = errorText(err);
        setError(text);
        setBoth(before);
        setMessages((prev) => [
          ...prev,
          { id: nextId(), role: 'assistant', kind: 'text', text, timestamp: Date.now() },
        ]);
      } finally {
        setLoading(false);
      }
    },
    [setBoth],
  );

  const viewExplanationNow = useCallback(async () => {
    const s = snapshotRef.current;
    if (!s) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.refresh(s.query_state.session_id);
      setBoth(res);
      setHistory((prev) =>
        prev.length > 0 ? prev.map((h, i) => (i === prev.length - 1 ? { ...h, snapshot: res } : h)) : prev,
      );
      setViewingTurn(null);
      if (res.explanation_plan) {
        setMessages((prev) => [
          ...prev,
          {
            id: nextId(),
            role: 'assistant',
            kind: 'explanation',
            explanation: res.explanation_plan,
            timestamp: Date.now(),
          },
        ]);
      }
    } catch (err) {
      const text = errorText(err);
      setError(text);
      setMessages((prev) => [
        ...prev,
        { id: nextId(), role: 'assistant', kind: 'text', text, timestamp: Date.now() },
      ]);
    } finally {
      setLoading(false);
    }
  }, [setBoth]);

  const reset = useCallback(() => {
    setBoth(null);
    setMessages([]);
    setError(null);
    setHistory([]);
    setViewingTurn(null);
  }, [setBoth]);

  return {
    snapshot,
    messages,
    loading,
    error,
    capabilities,
    history,
    viewingTurn,
    setViewingTurn,
    submitQuery,
    answerQuestion,
    viewExplanationNow,
    reset,
  };
}
