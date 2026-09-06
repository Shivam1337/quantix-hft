"""Static contract tests for the bounded Preact dashboard control plane."""
from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FrontendControlPlaneTests(unittest.TestCase):
    def test_shell_loads_the_compiled_preact_entrypoint(self):
        html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")

        self.assertIn('/static/dashboard-app.js?v=3.1.0', html)
        self.assertIn('/static/charts.js?v=3.1.0', html)
        self.assertNotIn('/static/app.js', html)

    def test_frontend_declares_preact_and_a_reproducible_build(self):
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

        self.assertEqual("10.29.8", package["dependencies"]["preact"])
        self.assertIn("build:frontend", package["scripts"])
        self.assertIn("build:styles", package["scripts"])
        self.assertIn("build:dashboard", package["scripts"])
        self.assertIn("test:frontend", package["scripts"])

    def test_stream_store_coalesces_updates_without_polling(self):
        source = (ROOT / "frontend" / "src" / "dashboard" / "store.js").read_text(encoding="utf-8")

        self.assertIn("requestAnimationFrame", source)
        self.assertIn("this.frameScheduled", source)
        self.assertIn("/api/system/stream", source)
        self.assertNotIn("setInterval", source)

    def test_frontend_source_files_stay_small_and_generated_assets_exist(self):
        source_files = list((ROOT / "frontend").rglob("*.js")) + list((ROOT / "frontend").rglob("*.jsx")) + list((ROOT / "frontend").rglob("*.css"))

        self.assertTrue(source_files)
        self.assertTrue(all(len(path.read_text(encoding="utf-8").splitlines()) <= 250 for path in source_files))
        self.assertGreater((ROOT / "app" / "static" / "dashboard-app.js").stat().st_size, 10_000)
        self.assertGreater((ROOT / "app" / "static" / "styles.css").stat().st_size, 10_000)

    def test_canvas_renderer_can_release_route_scoped_resources(self):
        source = (ROOT / "app" / "static" / "charts.js").read_text(encoding="utf-8")

        self.assertIn("destroy()", source)
        self.assertIn("resizeObserver?.disconnect()", source)
        self.assertIn("window.removeEventListener('resize', this.onResize)", source)


if __name__ == "__main__":
    unittest.main()
