"""Bounded authentication retries over a private PTY, without secret CLI arguments."""
import getpass
import json
import re
import time
from dataclasses import dataclass

from helper.common import HelperError, redact
from helper.terminal import PtySession, code_input

ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
PROMPTS = [
    ("cache", re.compile(r"(?:^|[\r\n])enter passphrase[^\r\n]{0,1500}:\s*", re.I)),
    ("otp", re.compile(r"(?:^|[\r\n])enter 2FA code:\s*", re.I)),
    ("account", re.compile(r"(?:^|[\r\n])enter Apple ID[^\r\n]*:\s*", re.I)),
    ("password", re.compile(r"(?:^|[\r\n])enter password:\s*", re.I)),
]


@dataclass
class Outcome:
    status: str
    reason: str = ""
    otp_sent: bool = False


def json_events(text):
    decoder = json.JSONDecoder()
    events = []
    for match in re.finditer(r"\{", ANSI.sub("", text)):
        try:
            obj, _ = decoder.raw_decode(ANSI.sub("", text)[match.start():])
            if isinstance(obj, dict):
                events.append(obj)
        except ValueError:
            continue
    return events


def classify_error(text, otp_sent=False):
    lower = text.lower()
    if any(x in lower for x in ("too many", "rate limit", "429", "account locked", "account is locked", "temporarily locked")):
        return "rate_limit"
    if any(x in lower for x in ("keychain", "passphrase", "failed to decrypt", "jwe authentication", "go-jose")):
        return "cache"
    if any(x in lower for x in ("verification code", "auth code", "2fa", "two-factor", "two factor")):
        return "otp"
    if any(x in lower for x in ("invalid credentials", "incorrect password", "invalid password", "authentication failed")):
        return "otp" if otp_sent else "credentials"
    if any(x in lower for x in ("not purchased", "license", "not available", "itemnotavailable", "app not found", "no purchases")):
        return "license"
    if any(x in lower for x in ("certificate", "tls", "ssl")):
        return "tls"
    if any(x in lower for x in ("timeout", "timed out", "connection", "no such host", "network")):
        return "network"
    return "tool_error"


ERROR_TEXT = {
    "rate_limit": "Apple 已限流或限制登录。请稍后再试；脚本不会继续重复请求。",
    "cache": "本机 ipatool 登录缓存未能解锁。请核对缓存口令（不是 Apple 密码）后重新运行；不要删除其他人的账户资料。",
    "credentials": "Apple 未接受账号或密码。请核对 Apple 帐号和常规密码（不是 App 专用密码）。",
    "otp": "Apple 未接受验证码，或本轮验证已过期。",
    "license": "Apple 未返回此账号可用的翻咔下载许可。需使用曾获取该应用的账号；公开下架后的下载资格由 Apple 决定。",
    "tls": "安全连接校验失败，请检查系统时间、网络及受信任证书；脚本不会关闭证书校验。",
    "network": "连接 Apple 或下载服务失败，请检查网络后重试。",
    "tool_error": "工具未完成操作。请查看脱敏日志中的错误；可能需要更新工具或处理 Apple 服务变化。",
}


class Controller:
    def __init__(self, executable, log, session_factory=PtySession, read_code=code_input,
                 read_password=getpass.getpass, sleep=time.sleep, otp_timeout=180):
        self.executable, self.log = executable, log
        self.session_factory, self.read_code = session_factory, read_code
        self.read_password, self.sleep = read_password, sleep
        self.otp_timeout = otp_timeout
        self.cache_phrase = None

    def run(self, arguments, email="", password="", mode="login"):
        session = self.session_factory([self.executable, "--format", "json", *arguments])
        pending, history, otp_sent = "", "", False
        last_data, last_notice = time.monotonic(), time.monotonic()
        succeeded = False
        try:
            while True:
                try:
                    chunk = session.read(0.2)
                except EOFError:
                    chunk = ""
                if chunk:
                    pending = (pending + ANSI.sub("", chunk))[-32768:]
                    history = (history + ANSI.sub("", chunk))[-65536:]
                    last_data = time.monotonic()
                    events = json_events(history)
                    succeeded = succeeded or any(x.get("success") is True for x in events)
                    matches = [(m.start(), kind, m) for kind, pattern in PROMPTS if (m := pattern.search(pending))]
                    if matches:
                        _, kind, match = min(matches, key=lambda x: x[0])
                        pending = pending[match.end():]
                        if kind == "account":
                            if mode != "login":
                                raise HelperError("LOGIN_REQUIRED", "下载阶段要求重新登录，请重新运行助手。")
                            session.sendline(email)
                        elif kind == "password":
                            session.sendline(password)
                        elif kind == "cache":
                            if self.cache_phrase is None:
                                self.log.event("CACHE_PROMPT", "工具需要本机缓存口令。首次使用可设置一个；它与 Apple 密码不同。")
                                self.cache_phrase = self.read_password("本机缓存解锁口令（输入不显示）：")
                                if not self.cache_phrase:
                                    raise HelperError("EMPTY_CACHE_PASSWORD", "缓存口令不能为空；已停止本轮操作。")
                                self.log.secrets.add(self.cache_phrase)
                            session.sendline(self.cache_phrase)
                        else:
                            self.log.event("OTP_REQUIRED", "请查看 Apple 受信任设备上的验证码。180秒内输入；回车不填或输入R将重新发起登录，Q退出。")
                            code = self.read_code("验证码（输入隐藏）：", self.otp_timeout)
                            if code is None or code == "" or code.lower() == "r":
                                return Outcome("restart", "timeout" if code is None else "blank", otp_sent)
                            if code.lower() == "q":
                                raise KeyboardInterrupt
                            for _ in range(2):
                                if re.fullmatch(r"[0-9]{6}", code):
                                    break
                                self.log.event("OTP_FORMAT", "验证码应为6位数字；回车不填可重新申请。")
                                code = self.read_code("验证码（输入隐藏）：", self.otp_timeout)
                                if code is None or code == "" or code.lower() == "r":
                                    return Outcome("restart", "blank", otp_sent)
                                if code.lower() == "q":
                                    raise KeyboardInterrupt
                            if not re.fullmatch(r"[0-9]{6}", code):
                                return Outcome("restart", "format", otp_sent)
                            self.log.secrets.add(code)
                            session.sendline(code)
                            otp_sent = True
                        last_data = time.monotonic()
                if time.monotonic() - last_notice >= 15:
                    self.log.event("APPLE_WAIT", "正在等待 Apple 响应；请留意设备或系统钥匙串授权提示。")
                    last_notice = time.monotonic()
                if time.monotonic() - last_data > (600 if mode == "download" else 300):
                    raise HelperError("TOOL_TIMEOUT", "工具长时间没有响应，已结束本轮操作。请检查网络或系统授权提示后重试。")
                if chunk == "" or not session.alive():
                    # Drain final output before evaluating success; a zero exit alone is insufficient.
                    for _ in range(10):
                        try:
                            tail = session.read(0.05)
                            if tail:
                                history = (history + ANSI.sub("", tail))[-65536:]
                        except EOFError:
                            break
                    succeeded = succeeded or any(x.get("success") is True for x in json_events(history))
                    return_code = session.returncode()
                    if return_code is None:
                        self.sleep(0.05)
                        return_code = session.returncode()
                    if return_code == 0 and succeeded:
                        return Outcome("success", otp_sent=otp_sent)
                    # Only the explicit error value is eligible for sanitized diagnostic output.
                    errors = [x.get("error") for x in json_events(history) if isinstance(x.get("error"), str)]
                    reason = classify_error(" ".join(errors) if errors else history[-1000:], otp_sent)
                    if errors:
                        self.log.event("TOOL_ERROR", ERROR_TEXT[reason], error_category=reason, return_code=return_code)
                    return Outcome("failed", reason, otp_sent)
        finally:
            session.close()

    def login(self, email, password, attempts=3):
        self.log.secrets.update((email, password))
        for attempt in range(1, attempts + 1):
            self.log.event("LOGIN_ATTEMPT", "正在向 Apple 发起登录请求。", attempt=attempt, max_attempts=attempts)
            outcome = self.run(["auth", "login"], email, password)
            if outcome.status == "success":
                self.log.event("LOGIN_OK", "Apple 帐号登录成功。")
                return
            if outcome.status == "restart" or outcome.reason == "otp":
                if attempt < attempts:
                    self.log.event("OTP_REISSUE", "本轮验证码未完成；将结束当前请求并重新登录，再次申请验证。Apple可能复用验证码或暂时限流。", reason=outcome.reason)
                    self.sleep(3)
                    continue
                raise HelperError("OTP_RETRIES_EXHAUSTED", "已达到3轮验证上限。请稍后重试，或按 Apple 官方说明在受信任设备获取验证码：https://support.apple.com/102606")
            raise HelperError("LOGIN_" + outcome.reason.upper(), ERROR_TEXT[outcome.reason])
