"""Official MCP-client transport check on the synthetic native browser, no models."""

import argparse
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
import threading
import uuid

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import agenttime.assistantbench_bridge as implementation


async def probe(output, url):
    (output / 'goal.txt').write_text('Synthetic development task: inspect the local qualification page and submit a short answer.\n')
    (output / 'home').mkdir(); (output / 'tmp').mkdir()
    module_root = Path(implementation.__file__).resolve().parent.parent
    environment = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': str(output / 'home'),
                   'TMPDIR': str(output / 'tmp'), 'PYTHONPATH': str(module_root),
                   'PYTHONDONTWRITEBYTECODE': '1',
                   'PLAYWRIGHT_BROWSERS_PATH': os.environ['PLAYWRIGHT_BROWSERS_PATH']}
    parameters = StdioServerParameters(command=sys.executable,
        args=['-m', 'agenttime.assistantbench_bridge', '--goal-file', str(output / 'goal.txt'),
              '--state-dir', str(output / 'attempt'), '--attempt-id', str(uuid.uuid4()),
              '--qualification-start-url', url], env=environment)
    with (output / 'server.stderr').open('w') as stderr:
        async with stdio_client(parameters, errlog=stderr) as (read, write):
            async with ClientSession(read, write) as client:
                initialized = await client.initialize()
                tools = await client.list_tools()
                names = [tool.name for tool in tools.tools]
                assert 'send_msg_to_user' in names and 'observe' in names and 'upload_file' not in names
                observation = await client.call_tool('observe', {})
                assert not observation.model_dump(by_alias=True).get('isError', False)
                assert [item.type for item in observation.content] == ['text', 'image']
                assert 'Fresh browser state' in observation.content[0].text
                blocked = await client.call_tool('goto', {'url': 'file:///private/forbidden'})
                assert blocked.model_dump(by_alias=True)['isError']
                result = await client.call_tool('send_msg_to_user', {'text': 'Synthetic MCP final answer.'})
                assert not result.model_dump(by_alias=True).get('isError', False)
                assert json.loads(result.content[0].text) == {'submitted': True}
                assert (output / 'attempt/final-answer.txt').read_text() == 'Synthetic MCP final answer.'
                after = await client.call_tool('click', {'bid': '1'})
                assert after.model_dump(by_alias=True)['isError']
                return {'protocol_version': initialized.model_dump(by_alias=True)['protocolVersion'], 'tool_names': names,
                    'checks_passed': ['official_client_initialization', 'typed_tool_listing', 'native_browser_observation',
                                      'file_navigation_rejected', 'native_final_answer_sealed', 'post_terminal_action_rejected']}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=False)
    html = Path(__file__).with_name('synthetic.html').read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/synthetic.html': self.send_error(404); return
            self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(html))); self.end_headers(); self.wfile.write(html)

        def log_message(self, *_): pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        # A development-test watchdog only. The bridge adds no natural-task cap.
        async def bounded_probe():
            return await asyncio.wait_for(probe(args.output, f'http://127.0.0.1:{server.server_port}/synthetic.html'), timeout=120)
        results = asyncio.run(bounded_probe())
        report = {'schema_version': 1, 'qualification': 'official_mcp_client_real_browser_synthetic_page',
                  'model_calls': 0, 'selected_task_inputs_used': 0, 'platform': platform.platform(),
                  'mcp_client_version': importlib.metadata.version('mcp'),
                  'source_sha256': hashlib.sha256(Path(implementation.__file__).read_bytes()).hexdigest(),
                  'study_ready': False, 'claude_tool_path_qualified': False, 'linux_worker_qualified': False,
                  'archive_restore_qualified': False, **results}
        (args.output / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))
    finally:
        server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__': main()
