from pathlib import Path
import json
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.connectors.engine_client import EngineClient


class TestEngineConnector(unittest.TestCase):
    @patch("app.connectors.engine_client.requests.Session")
    def test_get_load_script_http_transport(self, session_cls: Mock) -> None:
        response = Mock()
        response.json.return_value = {"script": "LOAD * FROM [Orders.qvd];"}
        response.raise_for_status.return_value = None
        session = Mock()
        session.get.return_value = response
        session_cls.return_value = session

        client = EngineClient("http://qlik", "", "")
        script = client.get_load_script("app1")

        self.assertIn("Orders.qvd", script)

    @patch("app.connectors.engine_session._write_temp", return_value="/tmp/fake.pem")
    @patch("app.connectors.engine_session._load_root_pem", return_value=b"root")
    @patch("app.connectors.engine_session._load_client_pem", return_value=(b"cert", b"key"))
    @patch("app.connectors.engine_session.websocket")
    def test_get_load_script_ws_transport_matches_enigma_flow(
        self, ws_module: Mock, _client_pem: Mock, _root_pem: Mock, _write_temp: Mock
    ) -> None:
        """Verify the enigma.js flow: OpenDoc(qNoData=True) → GetScript → CloseDoc."""
        ws = Mock()
        sent: list[dict] = []

        def _send(payload: str) -> None:
            sent.append(json.loads(payload))

        def _recv() -> str:
            last = sent[-1]
            method = last["method"]
            if method == "OpenDoc":
                return json.dumps({
                    "id": last["id"],
                    "result": {"qReturn": {"qType": "Doc", "qHandle": 1}},
                })
            if method == "GetScript":
                return json.dumps({
                    "id": last["id"],
                    "result": {"qScript": "LOAD * FROM [Sales.qvd];"},
                })
            if method == "CloseDoc":
                return json.dumps({"id": last["id"], "result": {"qReturn": True}})
            raise AssertionError(f"Unexpected method {method}")

        ws.send.side_effect = _send
        ws.recv.side_effect = _recv
        ws_module.create_connection.return_value = ws

        client = EngineClient(
            qlik_user="UserDirectory=corporate; UserId=srv-qlik",
            engine_host="ms16-p-0295.dcsg.com",
            engine_port=4747,
            engine_path="/app",
            client_pfx="client.pfx",
            pfx_password="pw",
            root_cer="root.cer",
        )
        script = client.get_load_script("app-42")

        self.assertEqual(script, "LOAD * FROM [Sales.qvd];")

        # URL connects directly to the engine host on port 4747 (no virtual proxy)
        call_kwargs = ws_module.create_connection.call_args
        url = call_kwargs.args[0]
        self.assertEqual(url, "wss://ms16-p-0295.dcsg.com:4747/app/app-42")
        self.assertNotIn("xrfkey", url)
        # Mutual-TLS: X-Qlik-User header + client certificate in sslopt
        header = call_kwargs.kwargs["header"]
        self.assertIn("X-Qlik-User: UserDirectory=corporate; UserId=srv-qlik", header)
        sslopt = call_kwargs.kwargs["sslopt"]
        self.assertIn("certfile", sslopt)
        self.assertIn("keyfile", sslopt)
        self.assertIn("ca_certs", sslopt)

        # Flow: OpenDoc with qNoData=True, then GetScript on returned handle, then CloseDoc
        methods = [call["method"] for call in sent]
        self.assertEqual(methods, ["OpenDoc", "GetScript", "CloseDoc"])
        self.assertEqual(sent[0]["handle"], -1)
        self.assertEqual(sent[0]["params"], {"qDocName": "app-42", "qNoData": True})
        self.assertEqual(sent[1]["handle"], 1)   # doc handle returned by OpenDoc
        self.assertEqual(sent[2]["handle"], 1)   # CloseDoc uses same handle
        ws.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
