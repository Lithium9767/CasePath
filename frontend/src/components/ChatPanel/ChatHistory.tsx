import { Button, Timeline } from 'antd';
import type { ConditionStatus, QueryConditionState, TurnSnapshot } from '@/types';
import { StatusTag } from '@/components/StatusTag';
import { conditionLabels } from '@/utils/contracts';

interface Props {
  history: TurnSnapshot[];
  viewingTurn: number | null;
  onView: (turn: number | null) => void;
}

interface Change {
  conditionId: string;
  from: ConditionStatus;
  to: ConditionStatus;
}

function diffStates(prev: QueryConditionState[], next: QueryConditionState[]): Change[] {
  const changes: Change[] = [];
  const nextMap = new Map(next.map((s) => [s.condition_id, s.status]));
  for (const p of prev) {
    const n = nextMap.get(p.condition_id);
    if (n !== undefined && n !== p.status) {
      changes.push({ conditionId: p.condition_id, from: p.status, to: n });
    }
  }
  return changes;
}

export function ChatHistory({ history, viewingTurn, onView }: Props) {
  if (history.length === 0) return null;

  const items = history.map((t, i) => {
    const labels = conditionLabels(t.snapshot);
    const qa = t.snapshot.query_state.dialogue_history.find((d) => d.turn_id === t.turn);
    const changes =
      i > 0
        ? diffStates(history[i - 1].snapshot.query_state.condition_states, t.snapshot.query_state.condition_states)
        : [];
    const isViewing = viewingTurn === t.turn;

    return {
      color: t.turn === 0 ? 'gray' : 'blue',
      children: (
        <div className="chat-history__item">
          {t.turn === 0 ? (
            <div className="chat-history__text">系统理解用户问题，生成候选请求权与条件状态</div>
          ) : (
            <>
              <div className="chat-history__q">问：{qa?.question ?? '（追问）'}</div>
              {qa?.answer && <div className="chat-history__a">答：{qa.answer}</div>}
            </>
          )}
          {changes.length > 0 && (
            <div className="chat-history__changes">
              {changes.map((c) => (
                <div key={c.conditionId} className="chat-history__change">
                  <span className="chat-history__change-name">{labels[c.conditionId] ?? c.conditionId}</span>
                  <StatusTag status={c.from} /> → <StatusTag status={c.to} />
                </div>
              ))}
            </div>
          )}
          <Button
            size="small"
            type={isViewing ? 'default' : 'link'}
            onClick={() => onView(isViewing ? null : t.turn)}
            style={{ paddingLeft: 0, height: 'auto' }}
          >
            {isViewing ? '取消回看' : '回看此轮状态'}
          </Button>
        </div>
      ),
    };
  });

  return (
    <div className="chat-history">
      <div className="chat-history__title">对话历史</div>
      <Timeline items={items} />
    </div>
  );
}
