"""Native tools only over MCP; all release, seal, grade and restore controls stay in-process."""

from functools import partial
from uuid import uuid4
from weakref import WeakKeyDictionary

from .backend import BackendError


class MCPService:
    def __init__(self, backend):
        # Optional dependencies are loaded only when explicitly creating this service.
        import anyio
        from mcp.server.lowlevel import Server
        from mcp import types

        self.backend = backend
        self.server = Server("agenttime-automationbench", version="1")
        self._epochs = WeakKeyDictionary()

        @self.server.list_tools()
        async def list_tools():
            return [types.Tool(**schema) for schema in backend.tool_schemas()]

        # Native normalization handles omitted optional parameters; suppress SDK error strings.
        @self.server.call_tool(validate_input=False)
        async def call_tool(name, arguments):
            context = self.server.request_context
            if context.session not in self._epochs:
                self._epochs[context.session] = str(uuid4())
            try:
                result = await anyio.to_thread.run_sync(
                    partial(
                        backend.call,
                        self._epochs[context.session],
                        context.request_id,
                        name,
                        arguments,
                    )
                )
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=result["result"])],
                    isError=result["is_error"],
                )
            except BackendError as exc:
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=str(exc))],
                    isError=True,
                )
            except Exception:
                return types.CallToolResult(
                    content=[
                        types.TextContent(
                            type="text",
                            text="Tool service unavailable; controller reconciliation required.",
                        )
                    ],
                    isError=True,
                )

    async def run_streams(self, read, write):
        await self.server.run(read, write, self.server.create_initialization_options())

    def http_app(
        self, *, allowed_hosts=("localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*")
    ):
        """Embed in a controller-owned sidecar; no control endpoint is published."""
        from contextlib import asynccontextmanager
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        from starlette.applications import Starlette
        from starlette.routing import Route
        from mcp.server.transport_security import TransportSecuritySettings

        manager = StreamableHTTPSessionManager(
            self.server,
            json_response=True,
            stateless=False,
            security_settings=TransportSecuritySettings(
                allowed_hosts=list(allowed_hosts), allowed_origins=[]
            ),
        )

        @asynccontextmanager
        async def lifespan(app):
            async with manager.run():
                yield

        # ASGI callable object keeps Starlette from wrapping it as a request function.
        class Endpoint:
            async def __call__(self, scope, receive, send):
                await manager.handle_request(scope, receive, send)

        return Starlette(
            routes=[Route("/mcp", Endpoint(), methods=["GET", "POST", "DELETE"])],
            lifespan=lifespan,
        )
