import { Tag } from 'antd';
import type { ConditionStatus } from '@/types';
import { STATUS_COLOR, STATUS_LABEL } from '@/utils/status';

export function StatusTag({ status }: { status: ConditionStatus }) {
  return (
    <Tag color={STATUS_COLOR[status]} style={{ marginInlineEnd: 0 }}>
      {STATUS_LABEL[status]}
    </Tag>
  );
}
