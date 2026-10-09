"""Real MCP protocol requests against the optional standalone backend, no model."""

import copy
import json
import os
from pathlib import Path
import sys
import unittest

from agenttime.automationbench.backend import Backend
from agenttime.automationbench.native import NativeBindings
from agenttime.automationbench.service import MCPService

SOURCE_ENV = os.environ.get("AT_AUTOMATIONBENCH_SOURCE") or os.environ.get(
    "AUTOMATIONBENCH_SOURCE"
)
SOURCE = Path(SOURCE_ENV) if SOURCE_ENV else None


class TransportTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        if sys.version_info < (3, 13) or SOURCE is None or not SOURCE.is_dir():
            raise unittest.SkipTest(
                "Optional pinned Python3.13 benchmark runtime required"
            )
        cls.native = NativeBindings(SOURCE)
        from automationbench.domains.finance.tasks import (
            get_fin_invoice_email_extract_task,
        )

        cls.task = get_fin_invoice_email_extract_task()

    async def test_real_protocol_duplicate_conflict_and_sessions(self):
        import anyio
        from mcp.shared.memory import create_client_server_memory_streams
        from mcp.shared.message import SessionMessage
        from mcp.types import JSONRPCMessage, JSONRPCRequest, JSONRPCNotification

        b = Backend(self.native, self.task, "transport-fixture")
        b.release()
        service = MCPService(b)

        async def session_calls(requests):
            async with create_client_server_memory_streams() as (client, server):
                async with anyio.create_task_group() as group:
                    group.start_soon(service.run_streams, *server)
                    read, write = client

                    async def request(ident, method, params):
                        await write.send(
                            SessionMessage(
                                JSONRPCMessage(
                                    JSONRPCRequest(
                                        jsonrpc="2.0",
                                        id=ident,
                                        method=method,
                                        params=params,
                                    )
                                )
                            )
                        )
                        message = await read.receive()
                        return message.message.root.model_dump()

                    init = await request(
                        0,
                        "initialize",
                        {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {
                                "name": "model-free-fixture",
                                "version": "1",
                            },
                        },
                    )
                    self.assertIn("result", init)
                    await write.send(
                        SessionMessage(
                            JSONRPCMessage(
                                JSONRPCNotification(
                                    jsonrpc="2.0", method="notifications/initialized"
                                )
                            )
                        )
                    )
                    tools = await request(10, "tools/list", {})
                    self.assertEqual(
                        {t["name"] for t in tools["result"]["tools"]},
                        {"api_search", "api_fetch", "base64_encode"},
                    )
                    for t in tools["result"]["tools"]:
                        self.assertNotIn("world", t["inputSchema"]["properties"])
                    out = []
                    for ident, name, args in requests:
                        out.append(
                            await request(
                                ident, "tools/call", {"name": name, "arguments": args}
                            )
                        )
                    group.cancel_scope.cancel()
                    return out

        requests = [
            (1, "base64_encode", {"text": "hello"}),
            (1, "base64_encode", {"text": "hello"}),
            (1, "base64_encode", {"text": "different"}),
            (2, "grade", {}),
            (
                3,
                "api_fetch",
                {
                    "method": "POST",
                    "url": "https://api.hubapi.com/crm/v3/objects/contacts",
                    "body": json.dumps({"properties": {"__dict__": {"secret": "x"}}}),
                },
            ),
            (4, "api_search", {"query": "gmail messages", "top_k": 1}),
            (
                5,
                "api_fetch",
                {
                    "method": "GET",
                    "url": "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                    "params": None,
                    "body": None,
                },
            ),
        ]
        replies = await session_calls(requests)
        self.assertEqual(replies[0], replies[1])
        self.assertFalse(replies[0]["result"]["isError"])
        self.assertTrue(all(replies[i]["result"]["isError"] for i in (2, 3, 4)))
        self.assertEqual(len(b.owner_state()["receipts"]), 3)
        self.assertFalse(
            any(
                "assertion" in json.dumps(r).lower() or "/private/" in json.dumps(r)
                for r in replies
            )
        )
        other = await session_calls([(1, "base64_encode", {"text": "new-session"})])
        self.assertFalse(other[0]["result"]["isError"])
        self.assertEqual(len(b.owner_state()["receipts"]), 4)
        b.seal()
        stopped = await session_calls([(1, "base64_encode", {"text": "too late"})])
        self.assertTrue(stopped[0]["result"]["isError"])
        restored = Backend.restore(self.native, self.task, b.checkpoint())
        self.assertEqual(restored.grade(), b.grade())

    async def test_http_application_exposes_only_mcp(self):
        b = Backend(self.native, self.task, "http-fixture")
        service = MCPService(b)
        app = service.http_app()
        self.assertEqual([route.path for route in app.routes], ["/mcp"])

    async def test_streamable_http_real_native_mutation_deduplicates(self):
        from starlette.testclient import TestClient

        task = copy.deepcopy(self.task)
        task["info"] = {
            "initial_state": {
                "google_sheets": {
                    "spreadsheets": [{"id": "s", "title": "Sheet"}],
                    "worksheets": [
                        {
                            "id": "w",
                            "spreadsheet_id": "s",
                            "title": "Data",
                            "headers": ["value"],
                        }
                    ],
                    "rows": [],
                }
            },
            "zapier_tools": [],
            "assertions": [],
        }
        b = Backend(self.native, task, "http-actual")
        b.release()
        service = MCPService(b)
        with TestClient(service.http_app()) as client:
            headers = {
                "accept": "application/json, text/event-stream",
                "host": "localhost",
            }

            def request(ident, method, params):
                return client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": ident,
                        "method": method,
                        "params": params,
                    },
                )

            response = request(
                0,
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "offline-test", "version": "1"},
                },
            )
            self.assertEqual(response.status_code, 200)
            headers["mcp-session-id"] = response.headers["mcp-session-id"]
            headers["mcp-protocol-version"] = "2025-06-18"
            client.post(
                "/mcp",
                headers=headers,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            )
            args = {
                "method": "POST",
                "url": "https://sheets.googleapis.com/v4/spreadsheets/s/values/w:append",
                "params": None,
                "body": json.dumps({"values": [["new"]]}),
            }
            first = request(
                7, "tools/call", {"name": "api_fetch", "arguments": args}
            ).json()
            repeat = request(
                7, "tools/call", {"name": "api_fetch", "arguments": args}
            ).json()
            self.assertFalse(first["result"]["isError"])
            self.assertEqual(first, repeat)
            self.assertEqual(
                len(b.owner_state()["current"]["world"]["google_sheets"]["rows"]), 1
            )
            self.assertEqual(len(b.owner_state()["receipts"]), 1)
            self.assertEqual(client.get("/grade").status_code, 404)
            self.assertEqual(client.get("/checkpoint").status_code, 404)


if __name__ == "__main__":
    unittest.main()
