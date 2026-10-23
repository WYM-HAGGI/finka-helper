# 翻咔下载与安装助手

使用你自己的 Apple 帐号下载翻咔原版 IPA，并通过 USB 安装到 iPhone。支持 macOS 和 Windows，无需预装 Python 或 Xcode。

[下载 v1.0.0 工具包](https://github.com/WYM-HAGGI/finka-helper/releases/download/v1.0.0/FinkaHelper-v1.0.0.zip) · [全部发布版本](https://github.com/WYM-HAGGI/finka-helper/releases) · [完整使用说明](先读我.md)

## 开始使用

1. 下载上面的 **FinkaHelper-v1.0.0.zip** 并完整解压。
2. Mac 双击 `启动-Mac.command`；Windows 双击 `Start-Windows.cmd`。
3. 按提示输入自己最初获取翻咔的 Apple 帐号、密码和验证码。
4. 等待下载完成通知，连接并解锁新 iPhone，处理手机上的信任提示，再选择目标设备安装。
5. 查看安装结果；成功后在手机打开翻咔并登录应用账号。

首次运行会自动准备独立运行环境、下载工具和手机通信组件。Windows 缺少手机连接服务时，会引导前往官方 Apple Devices 商店页面完成驱动安装。

## 已实现

- 检测系统、处理器、磁盘空间和运行环境，按平台准备依赖。
- 官方二进制下载后核验固定 SHA-256；安装包检查 ZIP、应用标识和主程序。
- 验证码空回车、输入 R、等待超时或输错后，结束本轮请求并重新登录，最多三轮；限流或锁定时停止。
- 下载完成通知、目标设备选择、安装进度、版本回读，以及失败原因和脱敏日志。
- 已有完整应用时保留，不抹除手机、不清理其他备份。

## 使用条件与验证范围

每个人应使用自己的 Apple 帐号，通常需要曾经获取过翻咔；是否继续提供已下架应用由 Apple 决定。工具不购买新许可，也不能保证重新登录后 Apple 一定弹出新验证码。包内不包含 IPA、任何人的账号或登录态。

已在 Apple Silicon Mac 从空工具目录完成环境准备；21 项本地自动测试通过，其中两项使用真实 Mac 子终端验证重试。原版 IPA 的安装方案此前已在 iPhone 上确认能够安装、打开和登录。

**新助手尚未在 Windows 实机完成 Apple 登录或 USB 安装验证。** Windows 已通过 PowerShell 语法与 x64 依赖解析检查；Windows 11 ARM 使用实验性的 x64 兼容路径。自动测试使用模拟 Apple/设备响应，不能代替真实账号、驱动与手机测试。

运行条件、验证码处理、日志位置和故障说明见 [先读我](先读我.md)。第三方来源见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。本项目并非 Apple 或翻咔官方工具。

## 开发测试

```sh
python -m unittest discover -s tests -v
```

自动测试不需要真实 Apple 凭据或 iPhone。当前两个真实 PTY 用例仅在 POSIX 平台运行，Windows 执行测试时会跳过这两项。

本项目助手源码采用 MIT 许可证。运行时下载的第三方组件保留各自许可证。
