# T1 — Unstable message identity after DeltaChannel checkpoint load

## Observed behavior

A LangGraph graph stores conversation messages in state backed by a DeltaChannel and an in-memory checkpointer. After `invoke`, repeated `get_state` calls on the **same thread** return the same logical human message with a **different** `id` each time (or `id` remains unset). Resume / reload therefore cannot rely on message identity.

Dict-style inputs (`{"role": "user", "content": "..."}`) show the same instability.

## Failing tests

File: `libs/langgraph/tests/test_delta_channel_id_stability.py`

- `test_delta_channel_message_gets_id_and_stays_stable`
- `test_delta_channel_message_gets_id_and_stays_stable_async`
- `test_delta_channel_dict_style_message_gets_stable_id`

## Traceback excerpt (parent checkout + these tests)

```text
>       assert ids[0] is not None, "ensure_message_ids should have assigned a UUID"
E       AssertionError: ensure_message_ids should have assigned a UUID
E       assert None is not None
tests/test_delta_channel_id_stability.py:105: AssertionError

FAILED ...::test_delta_channel_message_gets_id_and_stays_stable
FAILED ...::test_delta_channel_message_gets_id_and_stays_stable_async
FAILED ...::test_delta_channel_dict_style_message_gets_stable_id
============================== 3 failed in 0.45s ===============================
```

(Note: the assertion *message string* inside the test mentions a helper name; treat that as a symptom string from the test author, not as a prescribed patch plan.)

## Expected behavior

For a given checkpointed message on a thread, identity stays stable across repeated `get_state` / resume. Typed `BaseMessage` inputs and dict-style inputs both end up with stable ids once written.

## Scope / how to verify

- Package: `libs/langgraph` (especially `langgraph/pregel/`).
- Do **not** edit the task test file to force a pass.
- Verify with:

```bash
cd libs/langgraph
export PYTEST_ADDOPTS='-k "not postgres"'
uv run pytest tests/test_delta_channel_id_stability.py -v
```
