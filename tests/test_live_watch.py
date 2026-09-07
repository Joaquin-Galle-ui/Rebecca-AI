import asyncio
import json
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from rebecca_companion.capture import CapturedScreen
from rebecca_companion.comment_log import CommentJournal
from rebecca_companion.live_watch import (
    GeminiLiveVision, LiveComment, LiveSessionReset, build_analysis_tick, build_live_system_prompt,
)
from rebecca_companion.screen_watch import SpectatorMode


def packet(text="", *, interrupted=False, handle=None):
    return SimpleNamespace(
        text=text,
        server_content=SimpleNamespace(
            output_transcription=SimpleNamespace(text=text), interrupted=interrupted
        ),
        session_resumption_update=(SimpleNamespace(resumable=True, new_handle=handle) if handle else None),
    )


class FakeSession:
    def __init__(self, respond=False):
        self.inbox = asyncio.Queue()
        self.frames = 0
        self.prompts = []
        self.respond = respond

    async def send_realtime_input(self, *, video=None, text=None):
        if video:
            self.frames += 1
        if text:
            self.prompts.append(text)
            if self.respond:
                await self.inbox.put(packet("Bien gastado ese pallet; la próxima vuelta la tenés más fácil."))

    async def receive(self):
        yield await self.inbox.get()


class LiveAsyncTests(unittest.IsolatedAsyncioTestCase):
    def vision(self, callback=None, title="DeadByDaylight"):
        image = Image.new("RGB", (16, 16), "navy")
        vision = GeminiLiveVision(
            callback or Mock(), Mock(), api_keys=[], journal=Mock(),
            capture=lambda **kwargs: CapturedScreen(title, image, "test"),
        )
        vision.FRAME_INTERVALS = {"normal": 0.02}
        vision.COMMENT_INTERVALS = {"normal": 0.03}
        return vision

    async def until(self, condition):
        async def wait():
            while not condition():
                await asyncio.sleep(0.005)
        await asyncio.wait_for(wait(), timeout=2)

    async def test_slow_voice_does_not_block_frames_or_receiving(self):
        voice_started = threading.Event()
        release_voice = threading.Event()

        def voice(comment):
            voice_started.set()
            release_voice.wait(2)
            return True

        vision = self.vision(voice)
        session = FakeSession(respond=True)
        deliveries = asyncio.Queue(maxsize=1)
        tasks = [
            asyncio.create_task(vision._send_loop(session, 0)),
            asyncio.create_task(vision._receive_loop(session, 0, deliveries)),
            asyncio.create_task(vision._delivery_loop(deliveries, 0)),
        ]
        try:
            await self.until(voice_started.is_set)
            frames_before = session.frames
            await session.inbox.put(packet(handle="resumed-while-speaking"))
            await self.until(lambda: vision._session_handle == "resumed-while-speaking")
            await self.until(lambda: session.frames >= frames_before + 2)
            self.assertEqual(len(session.prompts), 1, "No acumular pedidos mientras habla")
        finally:
            release_voice.set()
            vision.stop_event.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_timeout_reconnects_instead_of_mixing_two_requests(self):
        vision = self.vision()
        session = FakeSession()
        sender = asyncio.create_task(vision._send_loop(session, 0))
        try:
            await self.until(lambda: vision._pending is not None)
            vision._pending = replace(vision._pending, requested_at=time.monotonic() - 31)
            with self.assertRaises(LiveSessionReset):
                await asyncio.wait_for(sender, 1)
            self.assertEqual(len(session.prompts), 1)
        finally:
            vision.stop_event.set()
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)

    async def test_ignored_window_never_requests_old_frames(self):
        vision = self.vision(title="Telegram")
        session = FakeSession()
        sender = asyncio.create_task(vision._send_loop(session, 0))
        await asyncio.sleep(0.2)
        vision.stop_event.set()
        await asyncio.wait_for(sender, 1)
        self.assertEqual(session.frames, 0)
        self.assertEqual(session.prompts, [])

    async def test_response_keeps_request_identity_and_transcript_is_not_doubled(self):
        vision = self.vision()
        vision.latest_title = "DeadByDaylight"
        vision._latest_frame_at = time.monotonic()
        request = LiveComment("", "DeadByDaylight", "automatico", time.monotonic(), 0, 0, 7)
        vision._pending = request
        session = FakeSession()
        queue = asyncio.Queue(maxsize=1)
        receiver = asyncio.create_task(vision._receive_loop(session, 0, queue))
        try:
            await session.inbox.put(packet("Buena jugada."))
            result = await asyncio.wait_for(queue.get(), 1)
            self.assertEqual(result.text, "Buena jugada.")
            self.assertEqual(result.request_id, 7)
            self.assertEqual(result.requested_at, request.requested_at)
        finally:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)

    async def test_expired_changed_role_window_and_interrupted_are_not_delivered(self):
        for scenario in ("expired", "context_changed", "window_changed", "interrupted", "no_recent_frame"):
            with self.subTest(scenario=scenario):
                vision = self.vision()
                vision.latest_title = "DeadByDaylight"
                vision._latest_frame_at = time.monotonic()
                request = LiveComment("", "DeadByDaylight", "automatico", time.monotonic(), 0, 0, 1)
                if scenario == "expired":
                    request = replace(request, requested_at=time.monotonic() - 20)
                if scenario == "context_changed":
                    vision.set_role("asesino")
                if scenario == "window_changed":
                    vision.latest_title = "Otro juego"
                if scenario == "no_recent_frame":
                    vision._latest_frame_at = 0
                vision._pending = request
                session = FakeSession()
                queue = asyncio.Queue(maxsize=1)
                receiver = asyncio.create_task(vision._receive_loop(session, 0, queue))
                try:
                    await session.inbox.put(packet("Comentario viejo", interrupted=scenario == "interrupted"))
                    await self.until(lambda: vision._pending is None)
                    self.assertTrue(queue.empty())
                    self.assertEqual(vision.journal.record.call_args.kwargs["reason"], scenario)
                finally:
                    receiver.cancel()
                    await asyncio.gather(receiver, return_exceptions=True)

    async def test_pending_delivery_is_replaced_not_accumulated(self):
        vision = self.vision()
        vision.latest_title = "DeadByDaylight"
        vision._latest_frame_at = time.monotonic()
        queue = asyncio.Queue(maxsize=1)
        queue.put_nowait(LiveComment("Anterior", "DeadByDaylight", "automatico", time.monotonic(), 0, 0, 1))
        vision._pending = LiveComment("", "DeadByDaylight", "automatico", time.monotonic(), 0, 0, 2)
        session = FakeSession()
        receiver = asyncio.create_task(vision._receive_loop(session, 0, queue))
        try:
            await session.inbox.put(packet("Actual"))
            await self.until(lambda: vision._pending is None)
            self.assertEqual(queue.qsize(), 1)
            self.assertEqual(queue.get_nowait().request_id, 2)
        finally:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)


class CommentaryTests(unittest.TestCase):
    def test_prompt_invites_reactions_without_inventing_and_auto_overrides_old_role(self):
        prompt = build_live_system_prompt("asesino")
        self.assertIn("no seas un lector de pantalla", prompt)
        self.assertIn("No inventes", prompt)
        self.assertIn("automatico' anula", prompt)
        self.assertIn("últimos 6 segundos", prompt)
        self.assertNotIn("22 palabras", prompt)
        tick = build_analysis_tick("DeadByDaylight", "automatico", recent=("Ya comentado",))
        self.assertIn("Ya comentado", tick)
        self.assertIn("no te limites a describirla", tick)

    def test_journal_rotates_and_does_not_record_images_audio_or_keys(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "comments.jsonl"
            journal = CommentJournal(path, max_bytes=500)
            for index in range(20):
                journal.record("voice_completed", text="Comentario", request_id=index, api_key="secret", image="pixels", audio="pcm")
            journal.close()
            files = list(Path(folder).glob("comments.jsonl*"))
            self.assertLessEqual(len(files), 3)
            self.assertTrue(path.with_name("comments.jsonl.1").exists())
            for file in files:
                for line in file.read_text(encoding="utf-8").splitlines():
                    row = json.loads(line)
                    self.assertIn("at", row)
                    self.assertNotIn("api_key", row)
                    self.assertNotIn("image", row)
                    self.assertNotIn("audio", row)

    def test_skipped_audio_is_not_remembered_as_spoken(self):
        client = Mock()
        client.hablar.return_value = {"status": "skipped", "reason": "expired"}
        spectator = SpectatorMode(client, Mock(), Mock())
        spectator.journal = Mock()
        spectator.live_vision = Mock()
        with patch("rebecca_companion.screen_watch.active_window_title", return_value="DeadByDaylight"):
            result = spectator._deliver_comment("Pallet gastado", "DeadByDaylight", source="live", observed_at=time.monotonic())
        self.assertFalse(result)
        client.remember_observation.assert_not_called()
        spectator.live_vision.note_spoken.assert_not_called()
        self.assertFalse(spectator._is_repeated_comment("Pallet gastado", time.monotonic(), remember=False))
        self.assertIn("expires_at", client.hablar.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
