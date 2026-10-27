"""Private child PTY; its raw output is never printed or written to logs."""
import codecs
import os
import select
import signal
import subprocess
import time


class PtySession:
    def __init__(self, command):
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.windows = os.name == "nt"
        if self.windows:
            from winpty import PtyProcess
            self.child = PtyProcess.spawn([str(x) for x in command], dimensions=(40, 8192),
                                         env={**os.environ, "TERM": "dumb", "NO_COLOR": "1"})
            self.pollable = self.child.fileobj
        else:
            import fcntl
            import pty
            import struct
            import termios
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 8192, 0, 0))
            try:
                self.child = subprocess.Popen([str(x) for x in command], stdin=slave, stdout=slave, stderr=slave,
                                              start_new_session=True, close_fds=True,
                                              env={**os.environ, "TERM": "dumb", "NO_COLOR": "1"})
            finally:
                os.close(slave)
            self.pollable = master

    def read(self, timeout=0.2):
        if not select.select([self.pollable], [], [], timeout)[0]:
            return None
        try:
            if self.windows:
                value = self.child.read(8192)
            else:
                value = self.decoder.decode(os.read(self.pollable, 8192))
            if not value:
                raise EOFError
            return value
        except (OSError, EOFError):
            raise EOFError from None

    def sendline(self, text):
        value = text + ("\r" if self.windows else "\n")
        if self.windows:
            self.child.write(value)
        else:
            os.write(self.pollable, value.encode("utf-8"))

    def alive(self):
        return self.child.isalive() if self.windows else self.child.poll() is None

    def returncode(self):
        return self.child.exitstatus if self.windows else self.child.poll()

    def process_id(self):
        return self.child.pid

    def close(self):
        if self.windows:
            self.child.close(force=True)
        else:
            if self.child.poll() is None:
                try:
                    os.killpg(self.child.pid, signal.SIGTERM)
                    self.child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(self.child.pid, signal.SIGKILL)
                    self.child.wait(timeout=3)
                except ProcessLookupError:
                    pass
            os.close(self.pollable)


def code_input(prompt, timeout=180):
    """Cancellable, masked OTP input. No background stdin reader survives a timeout."""
    import sys
    print(prompt, end="", flush=True)
    deadline = time.monotonic() + timeout
    if os.name == "nt":
        import msvcrt
        chars = []
        while time.monotonic() < deadline:
            if not msvcrt.kbhit():
                time.sleep(0.05)
                continue
            char = msvcrt.getwch()
            if char == "\x03":
                raise KeyboardInterrupt
            if char in ("\r", "\n"):
                print()
                return "".join(chars).strip()
            if char == "\b":
                if chars:
                    chars.pop()
                    print("\b \b", end="", flush=True)
            elif char in ("\x00", "\xe0"):
                msvcrt.getwch()
            elif char.isprintable():
                chars.append(char)
                print("*", end="", flush=True)
        while msvcrt.kbhit():
            msvcrt.getwch()
        print()
        return None
    import termios
    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)
    settings = termios.tcgetattr(fd)
    settings[3] &= ~termios.ECHO
    try:
        termios.tcsetattr(fd, termios.TCSANOW, settings)
        if select.select([sys.stdin], [], [], max(0, deadline - time.monotonic()))[0]:
            value = sys.stdin.readline()
            if value == "":
                raise EOFError
            return value.strip()
        termios.tcflush(fd, termios.TCIFLUSH)
        return None
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, original)
        print()
