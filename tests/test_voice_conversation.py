import unittest
from unittest import mock

from rebecca_companion.client import RebeccaClient
from rebecca_companion.voice_listener import VoiceListener, is_conversation_exit


class VoiceConversationTests(unittest.TestCase):
    @mock.patch('rebecca_companion.voice_listener.record_utterance', return_value=b'audio')
    def test_completed_pause_still_invalidates_late_transcription(self, _record):
        listener, on_text, _, _ = self.listener('Rebecca abrí Steam')
        def transcribe(_):
            listener.pause_for(0)
            return 'Rebecca abrí Steam'
        listener.transcribe = transcribe
        listener._listen_once(True)
        on_text.assert_not_called()

    @mock.patch('rebecca_companion.voice_listener.record_utterance', return_value=b'audio')
    def test_natural_greeting_recognizes_wake(self, _record):
        listener, on_text, _, _ = self.listener('Che, Rebecca, ¿viste eso?')
        listener._listen_once(True)
        on_text.assert_called_once_with('¿viste eso?')

    def listener(self, transcript, *, now=None):
        on_text, on_followup, statuses = mock.Mock(), mock.Mock(), []
        clock = (lambda: now[0]) if now is not None else __import__('time').monotonic
        listener = VoiceListener(lambda _audio: transcript, on_text, statuses.append,
                                 on_followup_text=on_followup, clock=clock)
        return listener, on_text, on_followup, statuses

    def test_exit_phrases_are_narrow_and_accent_insensitive(self):
        for phrase in ('listo gracias', 'Gracias Rebecca', 'dejémoslo acá',
                       'terminemos la charla', 'fin de la conversación',
                       'podés dejar de escuchar'):
            self.assertTrue(is_conversation_exit(phrase), phrase)
        for phrase in ('gracias por salvarme', 'está listo el generador',
                       'terminemos esta partida', 'Rebecca abre Steam'):
            self.assertFalse(is_conversation_exit(phrase), phrase)

    @mock.patch('rebecca_companion.voice_listener.record_utterance', return_value=b'audio')
    def test_one_wake_opens_followups_without_repeating_rebecca(self, _record):
        listener, on_text, on_followup, _ = self.listener('Rebecca, ¿viste eso?')
        listener._listen_once(True)
        on_text.assert_called_once_with('¿viste eso?')
        self.assertTrue(listener.conversation_active)
        listener.finish_processing()
        listener._paused_until = 0
        listener.transcribe = lambda _audio: '¿Qué harías ahora?'
        listener._listen_once(True)
        on_followup.assert_called_once_with('¿Qué harías ahora?')
        self.assertTrue(listener.processing)

    @mock.patch('rebecca_companion.voice_listener.record_utterance', return_value=b'audio')
    def test_explicit_rebecca_inside_open_chat_is_an_authorized_turn(self, _record):
        listener, on_text, on_followup, _ = self.listener('Rebecca abrí Steam')
        listener.begin_conversation()
        listener._listen_once(True)
        on_text.assert_called_once_with('abrí Steam')
        on_followup.assert_not_called()

    @mock.patch('rebecca_companion.voice_listener.record_utterance', return_value=b'audio')
    def test_exit_closes_chat_without_calling_any_model(self, _record):
        listener, on_text, on_followup, statuses = self.listener('listo, gracias Rebecca')
        listener.begin_conversation()
        listener._listen_once(True)
        self.assertFalse(listener.conversation_active)
        self.assertFalse(listener.processing)
        on_text.assert_not_called()
        on_followup.assert_not_called()
        self.assertTrue(any('Charla cerrada' in status for status in statuses))

    def test_silence_timeout_returns_to_wake_word(self):
        now = [100.0]
        listener, *_ = self.listener('', now=now)
        listener.begin_conversation()
        now[0] += 44
        self.assertFalse(listener._conversation_expired())
        now[0] += 2
        self.assertTrue(listener._conversation_expired())
        self.assertFalse(listener.conversation_active)

    @mock.patch('rebecca_companion.voice_listener.record_utterance', return_value=b'audio')
    def test_open_chat_has_hard_transcription_cap_against_game_audio(self, _record):
        listener, on_text, on_followup, statuses = self.listener('ruido hablado del juego')
        listener.begin_conversation()
        for _ in range(9):
            listener._paused_until = 0
            listener._listen_once(True)
            listener.finish_processing()
        self.assertEqual(on_followup.call_count, 8)
        self.assertFalse(listener.conversation_active)
        self.assertTrue(any('Charla cerrada' in status for status in statuses))
        on_text.assert_not_called()

    @mock.patch('rebecca_companion.voice_listener.record_utterance', return_value=b'audio')
    def test_manual_wake_can_open_same_chat_mode(self, _record):
        listener, on_text, _followup, _ = self.listener('Rebecca, comentá esa jugada')
        listener._listen_once(False)
        on_text.assert_called_once_with('comentá esa jugada')
        self.assertTrue(listener.conversation_active)

    @mock.patch('rebecca_companion.client.requests.post')
    def test_text_agent_cannot_execute_tool_during_wake_free_turn(self, post):
        response = mock.Mock(status_code=200)
        response.json.return_value = {'toolCall': {'accion': 'sistema', 'parametro': 'apagar'}}
        post.return_value = response
        client = RebeccaClient(personal_memory=mock.Mock())
        client.personal_memory.summary.return_value = ''
        with mock.patch.object(client, 'command') as command:
            answer = client.chat('apagá la PC', origen='desktop_voice_followup', allow_actions=False)
        command.assert_not_called()
        self.assertIn('decime ‘rebecca’', answer.casefold())


if __name__ == '__main__':
    unittest.main()
