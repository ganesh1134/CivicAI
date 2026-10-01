import io
import os
import tempfile
import unittest
from unittest.mock import patch

import backend as service


class PhotoUploadTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = os.path.join(self.temp_dir.name, "test.db")
        self.upload_dir = os.path.join(self.temp_dir.name, "uploads")
        self.database_patch = patch.object(service, "DATABASE", self.database)
        self.upload_patch = patch.object(service, "UPLOAD_DIR", self.upload_dir)
        self.database_patch.start()
        self.upload_patch.start()
        service.init_db()
        service.app.testing = True
        self.client = service.app.test_client()

    def tearDown(self):
        self.database_patch.stop()
        self.upload_patch.stop()
        self.temp_dir.cleanup()

    def test_upload_is_saved_and_returned_by_report_endpoints(self):
        response = self.client.post("/api/report", data={
            "category": "Pothole / Road Damage",
            "description": "Large pothole",
            "photo": (io.BytesIO(b"\x89PNG\r\n\x1a\ntest-image-bytes"), "issue.png"),
        }, content_type="multipart/form-data")

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        image_response = self.client.get(result["photo_url"])
        self.assertEqual(image_response.data, b"\x89PNG\r\n\x1a\ntest-image-bytes")
        image_response.close()
        self.assertEqual(
            self.client.get("/api/report/" + result["complaint_id"])
            .get_json()["report"]["photo_url"],
            result["photo_url"],
        )
        self.assertEqual(
            self.client.get("/api/reports").get_json()[0]["photo_url"],
            result["photo_url"],
        )

    def test_rejects_non_image_extension(self):
        response = self.client.post("/api/report", data={
            "photo": (io.BytesIO(b"not an image"), "issue.txt"),
        }, content_type="multipart/form-data")

        self.assertEqual(response.status_code, 400)

    def test_rejects_invalid_image_signature(self):
        response = self.client.post("/api/report", data={
            "photo": (io.BytesIO(b"not an image"), "issue.png"),
        }, content_type="multipart/form-data")

        self.assertEqual(response.status_code, 400)

    def test_rejects_files_over_10_mb(self):
        response = self.client.post("/api/report", data={
            "photo": (io.BytesIO(b"x" * (10 * 1024 * 1024 + 1)), "large.png"),
        }, content_type="multipart/form-data")

        self.assertEqual(response.status_code, 413)

    def test_accepts_photo_at_10_mb_limit(self):
        image = b"\x89PNG\r\n\x1a\n" + b"x" * (10 * 1024 * 1024 - 8)
        response = self.client.post("/api/report", data={
            "photo": (io.BytesIO(image), "limit.png"),
        }, content_type="multipart/form-data")

        self.assertEqual(response.status_code, 200)

    def test_json_report_without_photo_remains_supported(self):
        response = self.client.post("/api/report", json={
            "category": "Streetlight",
            "description": "Broken streetlight",
        })

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.get_json()["photo_url"])

    def test_image_without_api_key_does_not_default_to_pothole(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            response = self.client.post("/api/report", data={
                "category": "Let AI detect",
                "photo": (io.BytesIO(b"\x89PNG\r\n\x1a\nroad-image"), "road.png"),
            }, content_type="multipart/form-data")

        analysis = response.get_json()["ai_analysis"]
        self.assertEqual(analysis["status"], "not_configured")
        self.assertEqual(analysis["category"], "Needs AI review")
        self.assertEqual(analysis["priority"], 0)

    def test_clean_road_image_is_not_reported_as_an_issue(self):
        clean_road = {
            "status": "complete",
            "issue_detected": False,
            "category": "No issue detected",
            "confidence": 0.98,
            "summary": "The road appears intact with no visible civic defect.",
        }
        with patch.object(service, "analyze_image", return_value=clean_road):
            response = self.client.post("/api/report", data={
                "category": "Let AI detect",
                "photo": (io.BytesIO(b"\x89PNG\r\n\x1a\nroad-image"), "road.png"),
            }, content_type="multipart/form-data")

        analysis = response.get_json()["ai_analysis"]
        self.assertEqual(analysis["category"], "No issue detected")
        self.assertEqual(analysis["priority"], 0)
        self.assertEqual(analysis["department"], "No action needed")

    def test_gemini_clear_road_response_is_normalized(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            with patch("google.genai.Client") as client_factory:
                client_factory.return_value.interactions.create.return_value.output_text = (
                    '{"issue_detected": false, "category": "Pothole / Road Damage", '
                    '"confidence": 0.98, "summary": "The road appears intact."}'
                )
                result = service.analyze_image(b"image-bytes", "image/jpeg", "clear road")

        self.assertEqual(result["status"], "complete")
        self.assertFalse(result["issue_detected"])
        self.assertEqual(result["category"], "No issue detected")
        self.assertEqual(result["confidence"], 0.98)
        request = client_factory.return_value.interactions.create.call_args.kwargs
        self.assertEqual(request["response_format"]["mime_type"], "application/json")
        self.assertIn("issue_detected", request["response_format"]["schema"]["required"])


if __name__ == "__main__":
    unittest.main()
