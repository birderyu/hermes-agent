"""Native-poll clarify tests for PhotonAdapter.

iMessage has a native poll bubble (spectrum-ts `poll()` builder). A
multiple-choice ``clarify`` renders as that poll; the user taps a choice and
the vote streams back inbound as a ``poll_option`` event. These tests cover
both directions without spawning the Node sidecar or binding ports:

  * clarify polls resolve their registered prompt directly, and late changes
    remain owned by that closed prompt instead of becoming new user turns;
  * ordinary polls keep their native selection/change stream;
  * multi-select clarifies collect selections until the explicit completion
    option is tapped.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import MessageEvent, SendResult
from plugins.platforms.photon.adapter import PhotonAdapter


def _make_adapter(monkeypatch: pytest.MonkeyPatch) -> PhotonAdapter:
    monkeypatch.setenv("PHOTON_PROJECT_ID", "test-project-id")
    monkeypatch.setenv("PHOTON_PROJECT_SECRET", "test-project-secret")
    cfg = PlatformConfig(enabled=True, token="", extra={})
    return PhotonAdapter(cfg)


def _capture(
    adapter: PhotonAdapter, monkeypatch: pytest.MonkeyPatch
) -> List[MessageEvent]:
    captured: List[MessageEvent] = []

    async def fake_handle(event: MessageEvent) -> None:
        captured.append(event)

    monkeypatch.setattr(adapter, "handle_message", fake_handle)
    return captured


def _poll_option_event(
    *,
    title: str,
    selected: bool = True,
    poll_id: str = "spc-msg-poll",
    event_suffix: str = "vote-1",
    sender_id: str = "+155****4567",
) -> Dict[str, Any]:
    return {
        "messageId": f"{poll_id}:{sender_id}:option:{event_suffix}",
        "platform": "iMessage",
        "space": {"id": "+155****4567", "type": "dm", "phone": "+155****4567"},
        "sender": {"id": sender_id},
        "content": {
            "type": "poll_option",
            "title": title,
            "selected": selected,
            "pollTitle": "Pick one",
        },
        "timestamp": "2026-05-14T19:06:32.000Z",
    }


# ---------------------------------------------------------------------------
# Inbound: clarify polls and ordinary polls have separate lifecycles.


@pytest.mark.asyncio
async def test_clarify_poll_resolves_directly_and_late_change_stays_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.clarify_gateway as cg

    adapter = _make_adapter(monkeypatch)
    captured = _capture(adapter, monkeypatch)
    adapter._record_sent_message("spc-msg-poll")
    entry = cg.register(
        "clar-1", "sess-1", "Pick one", ["Cancel", "Proceed"]
    )
    adapter._remember_clarify_poll(
        "spc-msg-poll",
        clarify_id="clar-1",
        session_key="sess-1",
        choices=["Cancel", "Proceed"],
        multi_select=False,
        done_label=None,
    )

    try:
        await adapter._dispatch_inbound(
            _poll_option_event(title="Cancel", event_suffix="vote-1")
        )
        await adapter._dispatch_inbound(
            _poll_option_event(title="Proceed", event_suffix="vote-2")
        )

        assert entry.response == "Cancel"
        assert entry.event.is_set()
        assert captured == []
    finally:
        cg.clear_session("sess-1")


@pytest.mark.asyncio
async def test_ordinary_poll_preserves_multiple_selection_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The clarify fix must not impose first-vote-wins on native polls."""
    adapter = _make_adapter(monkeypatch)
    captured = _capture(adapter, monkeypatch)
    adapter._record_sent_message("spc-msg-poll")

    await adapter._dispatch_inbound(
        _poll_option_event(title="Cancel", event_suffix="vote-1")
    )
    await adapter._dispatch_inbound(
        _poll_option_event(title="Proceed", event_suffix="vote-2")
    )

    assert [event.text for event in captured] == ["Cancel", "Proceed"]


@pytest.mark.asyncio
async def test_group_ordinary_poll_preserves_each_selection_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _make_adapter(monkeypatch)
    captured = _capture(adapter, monkeypatch)
    adapter._record_sent_message("spc-msg-poll")

    first = _poll_option_event(title="A", sender_id="user-a")
    second = _poll_option_event(
        title="B", sender_id="user-b", event_suffix="vote-2"
    )
    first["space"] = {"id": "group-1", "type": "group", "phone": "+155****4567"}
    second["space"] = first["space"]

    await adapter._dispatch_inbound(first)
    await adapter._dispatch_inbound(second)

    assert [event.text for event in captured] == ["A", "B"]


@pytest.mark.asyncio
async def test_untracked_poll_vote_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Old polls cannot answer a later clarify after an adapter restart."""
    adapter = _make_adapter(monkeypatch)
    captured = _capture(adapter, monkeypatch)

    await adapter._dispatch_inbound(
        _poll_option_event(title="Proceed", poll_id="old-poll")
    )

    assert captured == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("title", "selected"),
    [("Proceed", False), ("", True)],
)
async def test_poll_deselection_and_empty_choice_are_dropped(
    monkeypatch: pytest.MonkeyPatch,
    title: str,
    selected: bool,
) -> None:
    adapter = _make_adapter(monkeypatch)
    captured = _capture(adapter, monkeypatch)
    adapter._record_sent_message("spc-msg-poll")

    await adapter._dispatch_inbound(
        _poll_option_event(title=title, selected=selected)
    )

    assert captured == []


# ---------------------------------------------------------------------------
# Outbound: send_clarify renders a native poll for choices.


def _stub_sidecar_poll(
    adapter: PhotonAdapter, monkeypatch: pytest.MonkeyPatch, *, ok: bool = True
) -> List[Tuple[str, str, list]]:
    calls: List[Tuple[str, str, list]] = []

    async def fake_send_poll(space_id: str, title: str, options: list):
        calls.append((space_id, title, list(options)))
        return SendResult(
            success=ok,
            message_id="spc-msg-poll" if ok else None,
            error=None if ok else "boom",
        )

    monkeypatch.setattr(adapter, "_sidecar_send_poll", fake_send_poll)
    return calls


def _stub_sidecar_text(
    adapter: PhotonAdapter, monkeypatch: pytest.MonkeyPatch
) -> List[Tuple[str, str]]:
    sends: List[Tuple[str, str]] = []

    async def fake_send(space_id: str, text: str):
        sends.append((space_id, text))
        return SendResult(success=True, message_id="spc-msg-text")

    monkeypatch.setattr(adapter, "_sidecar_send", fake_send)
    return sends


@pytest.mark.asyncio
async def test_send_clarify_with_choices_sends_native_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _make_adapter(monkeypatch)
    poll_calls = _stub_sidecar_poll(adapter, monkeypatch)

    marked: List[str] = []
    import tools.clarify_gateway as cg

    monkeypatch.setattr(cg, "mark_awaiting_text", lambda cid: marked.append(cid))

    result = await adapter.send_clarify(
        chat_id="+155****4567",
        question="Pick one",
        choices=["A", "B", "C"],
        clarify_id="clar-1",
        session_key="sess-1",
    )

    assert result.success
    assert len(poll_calls) == 1
    space_id, title, options = poll_calls[0]
    assert space_id == "+155****4567"
    assert title == "Pick one"
    assert options == ["A", "B", "C"]
    state = adapter._clarify_polls["spc-msg-poll"]
    assert state["clarify_id"] == "clar-1"
    assert state["multi_select"] is False
    # Typed replies remain available as Photon's free-form alternative.
    assert marked == ["clar-1"]


@pytest.mark.asyncio
async def test_multi_select_clarify_collects_until_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.clarify_gateway as cg

    adapter = _make_adapter(monkeypatch)
    captured = _capture(adapter, monkeypatch)
    poll_calls = _stub_sidecar_poll(adapter, monkeypatch)
    adapter._record_sent_message("spc-msg-poll")
    entry = cg.register(
        "clar-multi",
        "sess-multi",
        "Pick several",
        ["A", "B", "C"],
        multi_select=True,
    )

    try:
        result = await adapter.send_clarify(
            chat_id="+155****4567",
            question="Pick several",
            choices=["A", "B", "C"],
            clarify_id="clar-multi",
            session_key="sess-multi",
        )

        assert result.success
        assert len(poll_calls) == 1
        _space_id, title, options = poll_calls[0]
        assert "可多选" in title
        assert options == ["A", "B", "C", "✅ 完成选择"]

        await adapter._dispatch_inbound(
            _poll_option_event(title="A", event_suffix="select-a")
        )
        await adapter._dispatch_inbound(
            _poll_option_event(title="B", event_suffix="select-b")
        )
        await adapter._dispatch_inbound(
            _poll_option_event(
                title="A", selected=False, event_suffix="deselect-a"
            )
        )
        assert not entry.event.is_set()

        await adapter._dispatch_inbound(
            _poll_option_event(
                title="✅ 完成选择", event_suffix="submit"
            )
        )
        assert json.loads(entry.response or "") == ["B"]
        assert entry.event.is_set()

        # A change after submission is still owned by the closed clarify.
        await adapter._dispatch_inbound(
            _poll_option_event(title="C", event_suffix="late-change")
        )
        assert json.loads(entry.response or "") == ["B"]
        assert captured == []
    finally:
        cg.clear_session("sess-multi")


@pytest.mark.asyncio
async def test_multi_select_done_without_choices_submits_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.clarify_gateway as cg

    adapter = _make_adapter(monkeypatch)
    captured = _capture(adapter, monkeypatch)
    entry = cg.register(
        "clar-empty",
        "sess-empty",
        "Pick several",
        ["A", "B"],
        multi_select=True,
    )
    adapter._record_sent_message("spc-msg-poll")
    adapter._remember_clarify_poll(
        "spc-msg-poll",
        clarify_id="clar-empty",
        session_key="sess-empty",
        choices=["A", "B"],
        multi_select=True,
        done_label="✅ 完成选择",
    )

    try:
        await adapter._dispatch_inbound(
            _poll_option_event(title="✅ 完成选择", event_suffix="submit")
        )

        assert json.loads(entry.response or "") == []
        assert entry.event.is_set()
        assert captured == []
    finally:
        cg.clear_session("sess-empty")


@pytest.mark.asyncio
async def test_failed_native_clarify_falls_back_without_poll_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _make_adapter(monkeypatch)
    poll_calls = _stub_sidecar_poll(adapter, monkeypatch, ok=False)
    text_sends = _stub_sidecar_text(adapter, monkeypatch)

    result = await adapter.send_clarify(
        chat_id="+155****4567",
        question="Pick one",
        choices=["A", "B"],
        clarify_id="clar-fallback",
        session_key="sess-fallback",
    )

    assert result.success
    assert len(poll_calls) == 1
    assert len(text_sends) == 1
    assert adapter._clarify_polls == {}
