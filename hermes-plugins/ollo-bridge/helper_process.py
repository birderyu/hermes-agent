"""Track active capture helpers only, including late spawns after cancellation."""
import asyncio

from .protocol import BridgeError


class NotStarted(Exception):
    pass


class HelperProcesses:
    def __init__(self):
        self.active = set()
        self.cleanup = set()

    def is_active(self, request_id):
        return request_id in self.active

    async def _reap(self, launch, key):
        try:
            process = await launch
        except OSError:
            if key is not None:
                self.active.discard(key)
            return
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass  # Exit raced SIGKILL; wait still has to reap it.
        # A cancelled communicate() may leave a full pipe. Drain after kill so
        # asyncio's transport can close and wait() cannot stall on backpressure.
        for name in ('stdout', 'stderr'):
            pipe = getattr(process, name, None)
            if pipe is not None:
                while await pipe.read(65536):
                    pass
        await process.wait()
        if key is not None:
            self.active.discard(key)

    def _cleanup(self, launch, key):
        task = asyncio.create_task(self._reap(launch, key))
        self.cleanup.add(task)
        def finished(task):
            self.cleanup.discard(task)
            if not task.cancelled():
                task.exception()  # Cleanup failure leaves the request active.
        task.add_done_callback(finished)
        return task

    async def run(self, executable, data, budget, key=None):
        if key is not None:
            if key in self.active:
                raise BridgeError('write_outcome_unknown', 409, False)
            self.active.add(key)
        launch = asyncio.create_task(asyncio.create_subprocess_exec(executable,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE))
        try:
            try:
                process = await asyncio.wait_for(asyncio.shield(launch), budget.remaining())
            except OSError as exc:
                raise NotStarted('helper_not_started') from exc
            stdout, _ = await asyncio.wait_for(process.communicate(data), budget.remaining())
            return process.returncode, stdout
        finally:
            # Never cancel spawn and lose the child's handle. A late spawn is
            # killed by this retained reaper; the request stays active until wait ends.
            cleanup = self._cleanup(launch, key)
            try:
                await asyncio.wait_for(asyncio.shield(cleanup), timeout=0.5)
            except (asyncio.TimeoutError, asyncio.CancelledError, OSError):
                pass  # Cleanup remains owned; lookup cannot claim it has exited.
