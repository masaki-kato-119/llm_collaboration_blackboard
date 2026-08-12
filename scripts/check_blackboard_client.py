import anyio
from mcp.client.streamable_http import StreamableHTTPClient


async def main():
    client = StreamableHTTPClient("http://127.0.0.1:8000")
    try:
        tools = await client.list_tools()
        print('OK tools count:', len(tools))
        for t in tools:
            print('-', t.name)
    except Exception as e:
        print('ERROR connecting to MCP streamable-http:', e)

if __name__ == '__main__':
    anyio.run(main)
