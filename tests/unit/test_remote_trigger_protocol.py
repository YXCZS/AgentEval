import json

import pytest

from agent_eval_api.remote_trigger_protocol import (
    RemoteTriggerSignatureError,
    build_remote_trigger_delivery,
    verify_remote_trigger_delivery,
)


def test_valid_remote_trigger_delivery_is_verified_once() -> None:
    delivery = build_remote_trigger_delivery(
        {"experiment": {"id": "experiment-1"}},
        signing_secret="aet_test-secret",
        signature_header="X-Agent-Eval-Trigger-Signature",
        delivery_id="delivery-1",
        timestamp=1_700_000_000,
    )
    replay_cache: set[str] = set()

    assert verify_remote_trigger_delivery(
        delivery.headers,
        delivery.body,
        signing_secret="aet_test-secret",
        replay_cache=replay_cache,
        now=1_700_000_010,
    ) == {"experiment": {"id": "experiment-1"}}
    with pytest.raises(RemoteTriggerSignatureError, match="already processed"):
        verify_remote_trigger_delivery(
            delivery.headers,
            delivery.body,
            signing_secret="aet_test-secret",
            replay_cache=replay_cache,
            now=1_700_000_010,
        )


def test_expired_or_tampered_remote_trigger_delivery_is_rejected() -> None:
    delivery = build_remote_trigger_delivery(
        {"experiment": {"id": "experiment-1"}},
        signing_secret="aet_test-secret",
        signature_header="X-Agent-Eval-Trigger-Signature",
        delivery_id="delivery-1",
        timestamp=1_700_000_000,
    )
    with pytest.raises(RemoteTriggerSignatureError, match="expired"):
        verify_remote_trigger_delivery(
            delivery.headers,
            delivery.body,
            signing_secret="aet_test-secret",
            now=1_700_000_301,
        )

    tampered = json.dumps({"experiment": {"id": "experiment-2"}}).encode()
    with pytest.raises(RemoteTriggerSignatureError, match="signature is invalid"):
        verify_remote_trigger_delivery(
            delivery.headers,
            tampered,
            signing_secret="aet_test-secret",
            now=1_700_000_010,
        )
