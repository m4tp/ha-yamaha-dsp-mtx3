"""A fake MTX speaking just enough of the protocol for connection tests, built on asyncio.start_server."""

import asyncio


def default_reply(command: str) -> str:
    parts = command.split()
    match parts:
        case ["devstatus", "runmode"]:
            return 'OK devstatus runmode "normal"'
        case ["get" | "getn" as name, address, x, y]:
            return f"OK {name} {address} {x} {y} 1"
        case ["set" | "setn" as name, address, x, y, value]:
            return f'OK {name} {address} {x} {y} {value} "{value}"'
        case ["mtrstart", meter, _interval]:
            return f"OK mtrstart {meter}"
        case _:
            return f"OK {command}"


class FakeMtx:
    def __init__(self):
        self.commands: list[str] = []
        self.connections = 0
        self._writers: list[asyncio.StreamWriter] = []
        # async handler(fake, command, writer) -> True when it took care of the command (or chose not to reply)
        self.handler = None

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        return self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        self.drop_clients()
        self._server.close()
        await self._server.wait_closed()

    def drop_clients(self) -> None:
        for writer in self._writers:
            writer.close()
        self._writers.clear()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        self._writers.append(writer)
        try:
            while line := await reader.readline():
                command = line.decode().strip()
                self.commands.append(command)
                if self.handler is not None and await self.handler(self, command, writer):
                    continue
                writer.write(f"{default_reply(command)}\n".encode())
                await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()
