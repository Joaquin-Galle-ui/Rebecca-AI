import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "Isaac_IA_Python" / "ia_puente.py"
SPEC = importlib.util.spec_from_file_location("ia_puente", MODULE_PATH)
ia_puente = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ia_puente
SPEC.loader.exec_module(ia_puente)


class IsaacBridgeTests(unittest.TestCase):
    def test_parsea_telemetria_del_mod(self):
        line = 'detalle BOTCOOP_IA_DATOS:{"jugador_hp":5,"sala_actual":8,"enemigos_vivos":3,"hay_jefe":true,"items_visibles":[105],"jugadores":2}'
        state = ia_puente.parse_line(line)
        self.assertEqual(state.hp, 5)
        self.assertEqual(state.items, (105,))
        self.assertTrue(state.boss)
        self.assertEqual(state.players, 2)

    def test_detecta_dano_y_objetos_sin_reaccionar_a_cada_frame(self):
        client = mock.Mock()
        bridge = ia_puente.RebeccaIsaacBridge(client, speak=False, cooldown=10)
        bridge.event_for(ia_puente.IsaacState(hp=6, room=1))
        self.assertIn("pegaron", bridge.event_for(ia_puente.IsaacState(hp=4, room=1)))
        bridge.previous = ia_puente.IsaacState(hp=4, room=1)
        self.assertIn("pedestal", bridge.event_for(ia_puente.IsaacState(hp=4, room=1, items=(99,))))
        self.assertIsNone(bridge.event_for(ia_puente.IsaacState(hp=4, room=1, items=(99,))))

    def test_reaccion_usa_rebecca_y_puede_no_hablar(self):
        client = mock.Mock()
        client.chat.return_value = "Uh, se puso picante."
        bridge = ia_puente.RebeccaIsaacBridge(client, speak=False, cooldown=10)
        answer = bridge.react(ia_puente.IsaacState(hp=6, room=1))
        self.assertEqual(answer, "Uh, se puso picante.")
        client.chat.assert_called_once()
        client.hablar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
