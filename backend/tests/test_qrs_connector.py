from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.connectors.qrs_client import QrsClient


class TestQrsConnector(unittest.TestCase):
    @patch("app.connectors.qrs_client.requests.Session")
    def test_fetch_apps(self, session_cls: Mock) -> None:
        response = Mock()
        response.json.return_value = [{"id": "a1"}]
        response.raise_for_status.return_value = None
        session = Mock()
        session.get.return_value = response
        session_cls.return_value = session

        client = QrsClient("https://10.221.11.6/custom", qlik_user="CORPORATE\\srv-qlik")
        apps = client.fetch_apps()

        self.assertEqual([{"id": "a1"}], apps)

        # Header authentication through the /custom virtual proxy
        _, kwargs = session.get.call_args
        headers = kwargs["headers"]
        self.assertEqual(headers["X-Qlik-User"], "CORPORATE\\srv-qlik")
        self.assertEqual(headers["X-Qlik-Xrfkey"], "1234567890abcdef")
        self.assertEqual(kwargs["params"]["xrfkey"], "1234567890abcdef")


if __name__ == "__main__":
    unittest.main()
