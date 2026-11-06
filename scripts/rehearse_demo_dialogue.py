"""Interactive synthetic demo through actual HTTP routes and Live dependencies."""
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from casepath.api.configured import create_configured_app

sys.stdout.reconfigure(encoding='utf-8')
out = Path('artifacts/demo_rehearsal') / datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
out.mkdir(parents=True, exist_ok=False)
query = '我在健身房办了年卡，交了5000元，现在店关门了，还有3000元没消费，老板不肯退款。我想了解剩下的钱该怎么处理。'
records = []
with TestClient(create_configured_app()) as client:
    url, payload = '/v1/sessions', {'query':query}
    for turn in range(7):
        started = time.perf_counter()
        response = client.post(url,json=payload)
        body = response.json()
        records.append({'turn':turn,'request':payload,'http':response.status_code,
                        'seconds':time.perf_counter()-started,'response':body})
        (out/'transcript.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
        print('SAVED',out, 'HTTP',response.status_code,'SECONDS',round(records[-1]['seconds'],2),flush=True)
        if response.status_code not in (200,201):
            print(body,flush=True)
            break
        state = body['query_state']
        print('CONDITIONS',[(x['condition_id'],x['status']) for x in state['condition_states']],flush=True)
        print('EXPLANATION',json.dumps(body['explanation_plan'],ensure_ascii=False),flush=True)
        q = body.get('next_question')
        print('QUESTION',json.dumps(q,ensure_ascii=False),flush=True)
        if not q:
            break
        answer = input('ANSWER> ')
        url = f"/v1/sessions/{state['session_id']}/answers"
        payload = {'question_id':q['question_id'],'condition_id':q['condition_id'],'answer':answer}
