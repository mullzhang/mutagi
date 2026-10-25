from __future__ import annotations

import json

import pytest

import mutagi.execution.codex_session as codex_session
import mutagi.runtime.storage as storage
from tests.support.plans import RATES


@pytest.mark.parametrize("interrupt", [False, True])
def test_owned_server_protocol_records_final_or_interrupts(
    tmp_path, fake_codex, interrupt
):
    # Given the documented App Server protocol and a strict usage threshold.
    import sys
    import time

    fake_codex.write_text(f"""#!{sys.executable}
import sys,json
for line in sys.stdin:
 m=json.loads(line);method=m['method'];p=m.get('params',{{}})
 def emit(x):print(json.dumps(x),flush=True)
 if method=='initialize':emit({{'id':m['id'],'result':{{}}}})
 elif method=='thread/start':emit({{'id':m['id'],'result':{{'thread':{{'id':'owned-thread'}},'model':'gpt-6-astra'}}}})
 elif method=='turn/start':
  emit({{'id':m['id'],'result':{{'turn':{{'id':'owned-turn'}}}}}})
  emit({{'method':'thread/tokenUsage/updated','params':{{'threadId':'owned-thread','turnId':'owned-turn','tokenUsage':{{'total':{{'inputTokens':1000,'cachedInputTokens':0,'outputTokens':10,'reasoningOutputTokens':0,'totalTokens':1010}}}}}}}})
  if not {interrupt!r}:
   emit({{'method':'item/completed','params':{{'item':{{'type':'agentMessage','phase':'final_answer','text':'{{"done":true}}'}}}}}})
   emit({{'method':'turn/completed','params':{{'turn':{{'id':'owned-turn','status':'completed'}}}}}})
 elif method=='turn/interrupt':
  emit({{'id':m['id'],'result':{{}}}})
  emit({{'method':'turn/completed','params':{{'turn':{{'id':'owned-turn','status':'interrupted'}}}}}})
""")
    run = tmp_path / "owned"
    run.mkdir()
    storage.atomic_json(
        run / "state.json",
        {
            "model": "gpt-6-astra",
            "effort": "medium",
            "worktree": str(run),
            "exploration_deadline": time.time() + 15,
            "usage_budget": {
                "tokens": 1 if interrupt else 10000,
                "cost": 10,
                "rates": RATES,
            },
        },
    )
    storage.atomic_json(run / "schema.json", {"type": "object"})
    (run / "prompt.txt").write_text("Protocol test")
    # When one turn runs, then interrupted usage is not reported as a complete cost record.
    code = codex_session.execute(run)
    usage = json.loads((run / "usage.json").read_text())
    assert code == (75 if interrupt else 0)
    assert usage["complete"] is not interrupt
    assert usage["tokens"] == 1010
    assert (run / "result.json").exists() is not interrupt
