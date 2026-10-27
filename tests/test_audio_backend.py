import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backend = load("audio_backend")
local = load("local_audio_transcribe")
bilibili = load("bilibili_transcribe")


class AudioBackendTests(unittest.TestCase):
    def test_entry_points_use_bundled_backend(self):
        self.assertEqual(local.COMPANION, ROOT / "tools/audio_backend.py")
        self.assertEqual(bilibili.BACKEND, ROOT / "tools/audio_backend.py")
        self.assertTrue(callable(local.companion_module().infer))
        with patch.dict(os.environ, {"AI_LEARNING_SENSEVOICE_MODEL": "/tmp/configured-model"}):
            self.assertEqual(backend.model_path(None), Path("/tmp/configured-model"))
            self.assertEqual(backend.model_path("/tmp/explicit-model"), Path("/tmp/explicit-model"))

    def test_segmentation_preserves_all_frames_and_final_short_segment(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source = directory / "input.wav"
            frames = b"\x00\x00" * (16000 * 65 + 7)
            with wave.open(str(source), "wb") as stream:
                stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                stream.writeframes(frames)
            chunks = backend.split_wav(source, directory)
            restored = b""
            for chunk in chunks:
                with wave.open(str(chunk), "rb") as stream:
                    self.assertEqual(stream.getframerate(), 16000)
                    self.assertLessEqual(stream.getnframes(), 16000 * 30)
                    restored += stream.readframes(stream.getnframes())
            self.assertEqual(len(chunks), 3)
            self.assertEqual(restored, frames)

    def test_failed_or_partial_video_transcript_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "transcripts"
            with patch.object(bilibili, "OUTPUT_DIR", output):
                for result in ({"ok": False, "text": "partial"},
                               {"ok": True, "text": ""},
                               {"ok": True, "text": "partial", "partial": True}):
                    with self.subTest(result=result), self.assertRaises(ValueError):
                        bilibili.save_transcript(result, "https://example.com", "test")
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
