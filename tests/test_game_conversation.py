import asyncio
import time
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from rebecca_companion.client import RebeccaClient
from rebecca_companion.game_context import GameContext
from rebecca_companion.live_watch import LiveComment, LiveQuestion, LiveQuestionError
from rebecca_companion.screen_watch import SpectatorMode
from rebecca_companion.voice_listener import VoiceListener
from rebecca_companion.app import RebeccaCompanionApp
from rebecca_companion.interaction import InteractionCoordinator, InteractionPhase
from test_live_watch import FakeSession, packet
import test_live_watch as live_fixtures


class GameMemoryTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.memory = GameContext(clock=lambda: self.now)

    def test_bounded_ephemeral_context_not_personal_facts(self):
        self.memory.observe_window('DeadByDaylight')
        for i in range(30):
            self.memory.note('comentario_de_rebecca', f'Interpretación {i}')
        context = self.memory.summary()
        self.assertNotIn('Interpretación 0"', context)
        self.assertIn('interpretaciones, no hechos', context)
        self.now += 301
        self.assertEqual(self.memory.summary(), '')
        self.memory.observe_window('DeadByDaylight')
        self.assertNotIn('Interpretación', self.memory.summary())

    def test_switching_games_drops_old_context(self):
        self.memory.observe_window('DeadByDaylight')
        self.memory.note('usuario', 'Estoy jugando de asesino')
        self.memory.observe_window('Minecraft')
        self.assertNotIn('asesino', self.memory.summary())

    def test_role_corrections_must_be_first_person_affirmative_and_dbd(self):
        self.memory.observe_window('Dead by Daylight')
        for text in ('no soy asesino', 'el asesino me persigue', '¿soy asesino?'):
            self.assertIsNone(self.memory.correct_role(text))
        self.assertEqual(self.memory.correct_role('No estoy escapando, estoy jugando de asesino'), 'asesino')
        self.assertEqual(self.memory.correct_role('Soy superviviente'), 'superviviente')
        self.memory.observe_window('Minecraft')
        self.assertIsNone(self.memory.correct_role('Soy asesino'))

    def test_game_intent_not_every_conversation(self):
        self.assertFalse(self.memory.is_game_question('¿Viste eso?'))
        self.memory.observe_window('Minecraft')
        for text in ('¿Viste eso?', '¿Qué harías ahora?', '¿Por qué perdí esa persecución?', '¿A qué te referís?'):
            self.assertTrue(self.memory.is_game_question(text), text)
        for text in ('Hola, ¿cómo estás?', 'Estoy triste por mi pareja', 'Buscá precios de remeras'):
            self.assertFalse(self.memory.is_game_question(text), text)

    def test_text_chat_gets_separate_game_context(self):
        client = RebeccaClient(personal_memory=Mock(), game_context=self.memory)
        client.personal_memory.summary.return_value = ''
        self.memory.observe_window('Minecraft')
        self.memory.note('usuario', 'Quiero construir una casa')
        response = Mock(status_code=200)
        response.json.return_value = {'output': 'Podemos empezar por una base chica.'}
        with patch('rebecca_companion.client.requests.post', return_value=response) as post:
            client.chat('¿Y después?')
        payload = post.call_args.kwargs['json']
        self.assertIn('construir una casa', payload['game_context'])
        self.assertIn('CONTEXTO RECIENTE', payload['text'])
        self.assertEqual(payload['local_context'], '')
        self.assertIn('construir una casa', self.memory.summary())

    def test_comment_is_not_remembered_as_executed_action(self):
        client = RebeccaClient(game_context=self.memory)
        client.remember_observation('Minecraft', 'Ese refugio te deja mejor cubierto.')
        self.assertEqual(client._recent_actions, [])
        self.assertIn('comentario_de_rebecca', self.memory.summary())

    def test_spectator_does_not_deliver_while_user_is_recording(self):
        client = Mock()
        spectator = SpectatorMode(client, Mock(), Mock(), can_comment=lambda: False)
        spectator.journal = Mock()
        with patch('rebecca_companion.screen_watch.active_window_title', return_value='Minecraft'):
            self.assertFalse(spectator._deliver_comment('Hola', 'Minecraft', source='live', observed_at=time.monotonic()))
        client.hablar.assert_not_called()

    def test_noisy_non_wake_capture_does_not_delay_comments_forever(self):
        spectator = SpectatorMode(Mock(), Mock(), Mock())
        spectator.pause_for_conversation(False)
        self.assertEqual(spectator._resume_at, 0)
        spectator.pause_for_conversation(True)
        spectator.pause_for_conversation(False)
        self.assertGreater(spectator._resume_at, time.monotonic())


class ConversationAsyncTests(unittest.IsolatedAsyncioTestCase):
    vision = live_fixtures.LiveAsyncTests.vision
    until = live_fixtures.LiveAsyncTests.until

    async def test_question_uses_existing_frames_and_never_autospeaks(self):
        vision = self.vision(title='Minecraft')
        vision.connected_event.set()
        vision.pause_comments(True)
        session = FakeSession(respond=True)
        deliveries = asyncio.Queue(maxsize=1)
        tasks = [asyncio.create_task(vision._send_loop(session, 0)),
                 asyncio.create_task(vision._receive_loop(session, 0, deliveries))]
        try:
            await self.until(lambda: session.frames >= 2)
            result = await asyncio.to_thread(vision.ask_question, '¿Qué harías ahora?', 'Usuario busca refugio')
            self.assertIn('pallet', result)
            self.assertEqual(len(session.prompts), 1)
            self.assertIn('PREGUNTA DE Usuario', session.prompts[0])
            self.assertIn('Usuario busca refugio', session.prompts[0])
            self.assertTrue(deliveries.empty())
        finally:
            vision.stop()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_timeout_answer_is_dropped_and_cannot_become_an_auto_comment(self):
        vision = self.vision()
        vision.connected_event.set()
        vision.pause_comments(True)
        session = FakeSession()
        deliveries = asyncio.Queue(maxsize=1)
        tasks = [asyncio.create_task(vision._send_loop(session, 0)),
                 asyncio.create_task(vision._receive_loop(session, 0, deliveries))]
        try:
            await self.until(lambda: session.frames >= 2)
            with self.assertRaises(LiveQuestionError):
                await asyncio.to_thread(vision.ask_question, '¿Viste eso?', '', 0.2)
            self.assertIsNone(vision._question)
            await session.inbox.put(packet('Respuesta tardía'))
            await self.until(lambda: vision._pending is None)
            self.assertTrue(deliveries.empty())
        finally:
            vision.stop()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_pause_invalidates_in_flight_comment_and_keeps_frames(self):
        vision = self.vision()
        session = FakeSession()
        task = asyncio.create_task(vision._send_loop(session, 0))
        try:
            await self.until(lambda: vision._pending is not None)
            before = vision._pending
            vision.pause_comments(True)
            self.assertEqual(vision.invalid_reason(before), 'context_changed')
            frames = session.frames
            await self.until(lambda: session.frames >= frames + 2)
            self.assertEqual(len(session.prompts), 1)
        finally:
            vision.stop()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_stop_releases_waiting_question(self):
        vision = self.vision()
        vision.connected_event.set()
        vision._latest_frame_at = time.monotonic()
        task = asyncio.create_task(asyncio.to_thread(vision.ask_question, '¿Viste eso?', '', 2))
        await self.until(lambda: vision._question is not None)
        vision.stop()
        with self.assertRaises(LiveQuestionError):
            await asyncio.wait_for(task, 0.5)

    async def test_disconnected_question_does_not_start_new_api_session(self):
        vision = self.vision()
        with self.assertRaises(LiveQuestionError):
            vision.ask_question('¿Viste eso?')
        self.assertIsNone(vision.thread)

    async def test_typing_in_companion_uses_recent_game_not_chat_pixels(self):
        vision = self.vision(title='Minecraft')
        vision.connected_event.set()
        vision.pause_comments(True)
        session = FakeSession(respond=True)
        deliveries = asyncio.Queue(maxsize=1)
        tasks = [asyncio.create_task(vision._send_loop(session, 0)),
                 asyncio.create_task(vision._receive_loop(session, 0, deliveries))]
        try:
            await self.until(lambda: session.frames >= 2)
            before_capture = vision.capture
            vision.capture = lambda **kwargs: replace(before_capture(**kwargs), title='Rebecca Companion')
            await asyncio.sleep(0.12)
            frames = session.frames
            result = await asyncio.to_thread(vision.ask_question, '¿Qué harías ahora?')
            self.assertTrue(result)
            self.assertEqual(vision.latest_title, 'Minecraft')
            self.assertEqual(session.frames, frames)
            self.assertTrue(deliveries.empty())
        finally:
            vision.stop()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_question_answer_is_rejected_when_game_changes(self):
        vision = self.vision(title='Minecraft')
        vision.connected_event.set()
        vision.pause_comments(True)
        session = FakeSession()
        deliveries = asyncio.Queue(maxsize=1)
        tasks = [asyncio.create_task(vision._send_loop(session, 0)),
                 asyncio.create_task(vision._receive_loop(session, 0, deliveries))]
        try:
            await self.until(lambda: session.frames >= 2)
            answer = asyncio.create_task(asyncio.to_thread(vision.ask_question, '¿Viste eso?'))
            await self.until(lambda: vision._pending is not None)
            before_capture = vision.capture
            vision.capture = lambda **kwargs: replace(before_capture(**kwargs), title='Otro juego')
            await self.until(lambda: vision.latest_title == 'Otro juego')
            await session.inbox.put(packet('No debería entregarse'))
            with self.assertRaises(LiveQuestionError):
                await answer
            self.assertTrue(deliveries.empty())
        finally:
            vision.stop()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


class VoiceTurnTests(unittest.TestCase):
    @patch('rebecca_companion.voice_listener.record_utterance')
    def test_recording_guard_stays_on_until_transcription_and_delivery(self, record):
        observations = []
        listener = VoiceListener(lambda _: 'Rebecca, ¿viste eso?',
                                 lambda _: observations.append(listener.capturing_speech), Mock())
        def recording(*args, **kwargs):
            kwargs['on_speech_start']()
            return b'audio'
        record.side_effect = recording
        listener._listen_once(True)
        self.assertEqual(observations, [True])
        self.assertFalse(listener.capturing_speech)
        self.assertTrue(listener.processing)


class AppTurnTests(unittest.TestCase):
    def app(self):
        app = RebeccaCompanionApp.__new__(RebeccaCompanionApp)
        app.spectator = Mock()
        app.interactions = InteractionCoordinator()
        app.voice_listener = Mock()
        app.voice_listener.processing = False
        app.voice_listener.capturing_speech = False
        app._pending_voice_text = None
        app._pending_voice_poll_scheduled = False
        app._comment_in_progress = False
        app.busy = False
        app.root = Mock()
        app.status = Mock()
        return app

    def test_typed_text_waits_without_overwriting_or_losing_it(self):
        app = self.app()
        app.busy = True
        self.assertTrue(app.process_user_text('¿Viste eso?', 'desktop'))
        self.assertEqual(app._pending_voice_text, '¿Viste eso?')
        self.assertEqual(app._pending_user_origin, 'desktop')
        self.assertFalse(app.process_user_text('Otro mensaje', 'desktop'))
        self.assertEqual(app._pending_voice_text, '¿Viste eso?')
        app.spectator.pause_for_conversation.assert_called_with(True)

    def test_stale_lease_cannot_release_a_new_user_turn(self):
        app = self.app()
        old = app.interactions.try_begin('spectator', InteractionPhase.SPEAKING)
        app.interactions.finish(old)
        current = app.interactions.try_begin('user', InteractionPhase.THINKING)
        app._release_busy(old)
        self.assertTrue(app.interactions.busy)
        app.voice_listener.finish_processing.assert_not_called()
        app.spectator.pause_for_conversation.assert_not_called()
        app.interactions.finish(current)

    def test_pending_user_has_priority_over_auto_comment(self):
        app = self.app()
        app._pending_voice_text = '¿Qué harías?'
        self.assertFalse(app._can_auto_comment())
        app._pending_voice_text = None
        app.voice_listener.capturing_speech = True
        self.assertFalse(app._can_auto_comment())
        app.voice_listener.capturing_speech = False
        self.assertTrue(app._can_auto_comment())

    def test_game_question_uses_live_but_pc_command_still_uses_router(self):
        app = self.app()
        app.client = RebeccaClient(personal_memory=Mock())
        app.client.game_context.observe_window('DeadByDaylight')
        app.client.chat = Mock(return_value='Respuesta de chat')
        app.client.command = Mock(return_value={'status': 'success', 'message': 'Steam abierto'})
        app.client.hablar = Mock(return_value={'status': 'success'})
        app.spectator.running = True
        app.spectator.ask_about_game.return_value = 'Podrías cortar por el otro lado.'
        app.game_role = Mock()
        app.show_comment = Mock()
        app.root.after.side_effect = lambda delay, callback: callback()
        def thread(*args, **kwargs):
            return Mock(start=kwargs['target'])
        with patch('rebecca_companion.app.threading.Thread', side_effect=thread):
            self.assertTrue(app.process_user_text('¿Por qué perdí esa persecución?'))
            self.assertTrue(app.process_user_text('abrí Steam'))
        app.spectator.ask_about_game.assert_called_once()
        app.client.chat.assert_not_called()
        app.client.command.assert_called_once()
        self.assertEqual(app.client.command.call_args.args[0], 'abrir_programa')
        self.assertFalse(app.interactions.busy)

    @patch('rebecca_companion.voice_listener.record_utterance', return_value=b'audio')
    def test_stop_or_tts_during_transcription_cannot_issue_a_command(self, record):
        on_text = Mock()
        listener = VoiceListener(lambda _: '', on_text, Mock())
        def transcribe(_):
            listener.pause_for(5)
            return 'Rebecca abrí Steam'
        listener.transcribe = transcribe
        listener._listen_once(True)
        on_text.assert_not_called()
        self.assertFalse(listener.capturing_speech)


if __name__ == '__main__':
    unittest.main()
