import { Button, Space, Typography } from 'antd';
import { BulbOutlined, EyeOutlined } from '@ant-design/icons';
import type { QuestionCandidate } from '@/types';
import { formatScore } from '@/utils/status';

const { Text, Paragraph } = Typography;

interface Props {
  question: QuestionCandidate;
  disabled?: boolean;
  onAnswer: (answer: string) => void;
  onViewExplanation: () => void;
}

export function QuestionCard({ question, disabled, onAnswer, onViewExplanation }: Props) {
  return (
    <div className="question-card">
      <div className="question-card__head">
        <BulbOutlined style={{ color: '#1e5eff', marginRight: 6 }} />
        <Text strong>系统追问</Text>
        <span className="question-card__utility">
          Utility {formatScore(question.utility)}
        </span>
      </div>
      <Paragraph className="question-card__text">{question.question}</Paragraph>
      <div className="question-card__why">
        <Text type="secondary">为什么要问这个：</Text>
        <Text type="secondary">{question.why_asked}</Text>
      </div>
      {question.score_components.length > 0 && (
        <div className="question-card__components">
          {question.score_components.map((c) => (
            <div key={c.name} className="question-card__component">
              <span className="question-card__component-name">{c.name}</span>
              <span
                className="question-card__component-value"
                style={{ color: c.value >= 0 ? '#52c41a' : '#ff4d4f' }}
              >
                {c.value >= 0 ? '+' : ''}
                {formatScore(c.value)}
              </span>
            </div>
          ))}
        </div>
      )}
      <Space direction="vertical" style={{ width: '100%' }} size={8}>
        {question.options.map((opt) => (
          <Button
            key={opt}
            block
            disabled={disabled}
            onClick={() => onAnswer(opt)}
            className="question-card__option"
          >
            {opt}
          </Button>
        ))}
      </Space>
      <Button
        type="link"
        icon={<EyeOutlined />}
        disabled={disabled}
        onClick={onViewExplanation}
        style={{ paddingLeft: 0, marginTop: 8 }}
      >
        暂不回答，立即查看当前解释
      </Button>
    </div>
  );
}
