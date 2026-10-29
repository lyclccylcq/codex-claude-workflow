"""Stream a bounded CLI process to private files while draining both output pipes."""
import os
from pathlib import Path
import signal
import subprocess
import threading

import ai_trace


class Interrupted(ValueError):
    def __init__(self, stdout, stderr):
        self.stdout, self.stderr = stdout, stderr


def capture(argv, cwd, timeout, env, stdout_path, stderr_path, on_line):
    outputs = [bytearray(), bytearray()]
    errors = []
    stopped = False
    paths = [Path(stdout_path), Path(stderr_path)]
    handles = []
    process = None
    readers = []

    def drain(index, pipe, target):
        pending = b""
        callback = on_line if index == 0 else None
        try:
            while True:
                chunk = os.read(pipe.fileno(), 65536)
                if not chunk:
                    break
                outputs[index].extend(chunk)
                target.write(chunk)
                if callback:
                    pending += chunk
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        try:
                            callback(line)
                        except Exception as exc:
                            errors.append(exc)
                            callback = None  # Still drain/preserve output after a trace failure.
                            break
            if callback and pending:
                callback(pending)
        except Exception as exc:
            errors.append(exc)
        finally:
            pipe.close()

    def stop(sig):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass

    try:
        for path in paths:
            target = ai_trace.safe_file(path.parent, path.name)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            handles.append(os.fdopen(fd, "wb", buffering=0))
        process = subprocess.Popen([str(x) for x in argv], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        for index, pipe in enumerate((process.stdout, process.stderr)):
            thread = threading.Thread(target=drain, args=(index, pipe, handles[index]), daemon=True)
            readers.append(thread)
            thread.start()
        try:
            process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            stopped = True
            stop(signal.SIGTERM)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                stop(signal.SIGKILL)
                process.wait(timeout=2)
        for thread in readers:
            thread.join(timeout=2)
        if any(thread.is_alive() for thread in readers):
            stopped = True
            stop(signal.SIGKILL)  # A child must not retain pipes after its parent exits.
            for thread in readers:
                thread.join(timeout=2)
        if stopped:
            raise Interrupted(bytes(outputs[0]), bytes(outputs[1]))
        if errors:
            raise OSError("stream trace failed; partial output is retained") from errors[0]
        return process.returncode, bytes(outputs[0]), bytes(outputs[1])
    finally:
        if process is not None and process.poll() is None:
            stop(signal.SIGKILL)
            process.wait(timeout=2)
        for handle in handles:
            handle.close()
