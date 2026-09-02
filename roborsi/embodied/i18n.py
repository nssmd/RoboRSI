"""Lightweight i18n — translated string tables for CLI / onboard wizard.

Was a folder (i18n/ + common.json + setup.json); collapsed to one module with the
tables embedded — two tiny JSON files didn't warrant a package. `from
roborsi.i18n import t` is unchanged."""
from __future__ import annotations

_DEFAULT_LANG = "en"

_STRINGS: dict[str, dict[str, str]] = {
    "arms": {
        "zh": "机械臂",
        "en": "Arms"
    },
    "cameras": {
        "zh": "摄像头",
        "en": "Cameras"
    },
    "leader": {
        "zh": "主动臂",
        "en": "Leader"
    },
    "follower": {
        "zh": "从动臂",
        "en": "Follower"
    },
    "connected": {
        "zh": "已连接",
        "en": "Connected"
    },
    "disconnected": {
        "zh": "未连接",
        "en": "Disconnected"
    },
    "scanning": {
        "zh": "扫描中...",
        "en": "Scanning..."
    },
    "noPortsFound": {
        "zh": "未发现串口设备",
        "en": "No serial ports found"
    },
    "noCamerasFound": {
        "zh": "未发现摄像头",
        "en": "No cameras found"
    },
    "motorsFound": {
        "zh": "个电机",
        "en": "motors"
    },
    "portLabel": {
        "zh": "端口",
        "en": "Port"
    },
    "selectEmbodimentType": {
        "zh": "选择具身类型",
        "en": "Select embodiment type"
    },
    "arm": {
        "zh": "机械臂",
        "en": "Robot Arm"
    },
    "hand": {
        "zh": "灵巧手",
        "en": "Dexterous Hand"
    },
    "notSupportedYet": {
        "zh": "该类型暂不支持，请选择其他类型。",
        "en": "This type is not supported yet. Please select another type."
    },
    "handSoon": {
        "zh": "灵巧手（即将支持）",
        "en": "Dexterous Hand (coming soon)"
    },
    "humanoidSoon": {
        "zh": "人形（即将支持）",
        "en": "Humanoid (coming soon)"
    },
    "mobileSoon": {
        "zh": "移动底盘（即将支持）",
        "en": "Mobile Base (coming soon)"
    },
    "selectModel": {
        "zh": "选择型号 ({n}):",
        "en": "Select model ({n}):"
    },
    "scanningModel": {
        "zh": "\n正在扫描 {model} 硬件...",
        "en": "\nScanning for {model} hardware..."
    },
    "foundPorts": {
        "zh": "发现 {ports} 个串口和 {cameras} 个摄像头。",
        "en": "Found {ports} serial port(s) and {cameras} camera(s)."
    },
    "alreadyBound": {
        "zh": "（已有 {count} 个设备已识别，已跳过）",
        "en": "({count} device(s) already configured, skipped)"
    },
    "unassignedPorts": {
        "zh": "{n} 个未分配端口，晃动一个机械臂...",
        "en": "{n} unassigned port(s). Move an arm..."
    },
    "detectedMotion": {
        "zh": "检测到动作: {port}",
        "en": "Detected motion on: {port}"
    },
    "selectRole": {
        "zh": "选择角色:",
        "en": "Select role:"
    },
    "role_follower": {
        "zh": "从 (follower)",
        "en": "Follower"
    },
    "role_leader": {
        "zh": "主 (leader)",
        "en": "Leader"
    },
    "aliasPrompt": {
        "zh": "  别名 (如 left, right):",
        "en": "  Alias (e.g. left, right):"
    },
    "retryPrompt": {
        "zh": "  重试? (y/n): ",
        "en": "  Retry? (y/n): "
    },
    "timeout": {
        "zh": "  超时，未检测到动作。",
        "en": "  Timeout -- no event detected."
    },
    "skipped": {
        "zh": "  已跳过。",
        "en": "  Skipped."
    },
    "assigned": {
        "zh": "  已分配: {alias} -> {spec}",
        "en": "  Assigned: {alias} -> {spec}"
    },
    "cameraNaming": {
        "zh": "\n--- 摄像头命名 ---",
        "en": "\n--- Camera Naming ---"
    },
    "cameraSidePrompt": {
        "zh": "  摄像头 {index} 装在哪? (left/right/single):",
        "en": "  Which arm is camera {index} mounted on? (left/right/single):"
    },
    "cameraNamePrompt": {
        "zh": "  摄像头 {index} 的名称 (前缀 {prefix}, 回车跳过):",
        "en": "  Name for camera {index} (prefix {prefix}, or Enter to skip):"
    },
    "commitPrompt": {
        "zh": "提交这些分配? (y/n):",
        "en": "Commit these assignments? (y/n):"
    },
    "assignments": {
        "zh": "\n{count} 个分配:",
        "en": "\n{count} assignment(s):"
    },
    "resultCommitted": {
        "zh": "配置完成。{count} 个绑定已写入配置。",
        "en": "Setup complete. {count} binding(s) committed to manifest."
    },
    "resultCancelled": {
        "zh": "用户取消了配置。",
        "en": "Setup cancelled."
    },
    "resultTimeout": {
        "zh": "动臂识别超时。发现 {ports} 个串口和 {cameras} 个摄像头，但未完成分配。",
        "en": "Motion detection timed out. Found {ports} serial port(s) and {cameras} camera(s), but no assignments were made."
    },
    "resultNoHardware": {
        "zh": "未发现匹配的硬件设备。",
        "en": "No matching hardware found."
    },
    "resultNotSupported": {
        "zh": "该具身类型暂未支持。",
        "en": "This embodiment type is not yet supported."
    },
    "noAssignments": {
        "zh": "未做任何分配。",
        "en": "No assignments made."
    },
    "camera": {
        "zh": "摄像头",
        "en": "Camera"
    },
    "noConfiguredDevices": {
        "zh": "暂无配置设备",
        "en": "No configured devices"
    },
    "serialPermissionDenied": {
        "zh": "当前用户 {user} 不在 dialout 组，无法访问串口。请执行: sudo usermod -aG dialout {user}，然后重新登录或重启服务。",
        "en": "User '{user}' is not in the 'dialout' group. Fix: sudo usermod -aG dialout {user} && re-login or restart the service."
    },
    "permTitle": {
        "zh": "设备权限",
        "en": "Device Permissions"
    },
    "permSerial": {
        "zh": "串口访问",
        "en": "Serial Port Access"
    },
    "permCamera": {
        "zh": "摄像头访问",
        "en": "Camera Access"
    },
    "permGranted": {
        "zh": "已授权",
        "en": "Granted"
    },
    "permDenied": {
        "zh": "未授权",
        "en": "Denied"
    },
    "permNoDevice": {
        "zh": "未检测到设备",
        "en": "No devices detected"
    },
    "permDeviceCount": {
        "zh": "{count} 个设备",
        "en": "{count} device(s)"
    },
    "permFix": {
        "zh": "修复",
        "en": "Fix"
    },
    "permFixing": {
        "zh": "修复中...",
        "en": "Fixing..."
    },
    "permFixFailed": {
        "zh": "自动修复失败，请在终端执行以下命令后重启服务:",
        "en": "Auto-fix failed. Run this command in terminal, then restart the service:"
    }
}


def t(key: str, lang: str = "en", **kwargs: object) -> str:
    """Look up a translated string, format with kwargs. Falls back to English,
    then to the key itself."""
    entry = _STRINGS.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry.get(_DEFAULT_LANG) or key
    return text.format(**kwargs) if kwargs else text
