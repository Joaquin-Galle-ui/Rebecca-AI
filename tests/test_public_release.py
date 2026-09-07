import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PublicReleaseTests(unittest.TestCase):
    def test_private_runtime_files_are_absent(self):
        # La base vacía puede aparecer al importar Core durante la suite; Git la ignora.
        forbidden_names = {".env", "personal_memory.json", "settings.json"}
        for path in ROOT.rglob("*"):
            if path.is_file():
                self.assertNotIn(path.name, forbidden_names, str(path))
                self.assertNotIn("__pycache__", path.parts, str(path))
                self.assertNotIn(".wwebjs_auth", path.parts, str(path))

    def test_known_private_markers_are_absent(self):
        markers = (
            "factoid-sibling-cadillac",
            "8858341677",
            "C:\\Users\\Emanuel",
            "E:\\documentos 2.0",
            "Joaco",
            "Hurlingham",
            "JZ03fb4o1Shc413W",
            "0oVW8OPRuOCvDDI0",
            "4y6qnXY7KVmGiGFq",
        )
        for path in ROOT.rglob("*"):
            if path.resolve() == Path(__file__).resolve():
                continue
            if path.is_file() and path.suffix.lower() in {".py", ".json", ".js", ".md", ".txt", ".ps1", ".bat"}:
                text = path.read_text(encoding="utf-8", errors="replace")
                for marker in markers:
                    self.assertNotIn(marker.casefold(), text.casefold(), f"{marker} en {path}")
                self.assertIsNone(re.search(r"AIza[0-9A-Za-z_-]{20,}", text), str(path))
                self.assertIsNone(re.search(r"gsk_[0-9A-Za-z]{20,}", text), str(path))

    def test_workflows_are_disabled_and_detached(self):
        workflows = list((ROOT / "n8n-workflows").glob("*.json"))
        self.assertTrue(workflows)
        for path in workflows:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(data.get("active"), path.name)
            for key in ("id", "versionId", "activeVersionId", "meta", "pinData"):
                self.assertNotIn(key, data, f"{path.name}: {key}")
            for node in data.get("nodes", []):
                self.assertNotIn("webhookId", node, f"{path.name}: {node.get('name')}")
                for credential in (node.get("credentials") or {}).values():
                    self.assertNotIn("id", credential, f"{path.name}: {node.get('name')}")
                    self.assertTrue(credential.get("name", "").startswith("CONFIGURE_ME"))


if __name__ == "__main__":
    unittest.main()
