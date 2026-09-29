# -*- coding: utf-8 -*-
"""profiles.py — 多套配置方案（B5 / v2.0.9.0）：教室 / 宿舍 / 家里，一键切换。

为什么方案只装「位置相关字段」：
    账号、密码、网关地址在哪儿都一样（`password.txt` 更不该跟着方案跑 ✗）；
    真正会变的只有三件事 —— 要不要求网络位置守卫、白名单是什么、多久检查一次。
    所以方案 = 这几个键的快照，切换 = 把它们写回 `config.json`，**其余字段一律不动** ✓。

两种方案：
    - **手动方案**（`match_ssids` 为空）：只在用户点「应用」时生效；
    - **自动方案**（填了 `match_ssids`，且总开关 `profiles_auto_switch` 开着）：
      每次检查发现当前 Wi-Fi 名命中某个自动方案 → 自动切过去并写一行日志。

本模块只放**纯逻辑**（不碰文件、不碰网络）；读写 `config.json` 由注入的
`_load_config` / `_save_config` 完成，校验复用注入的 `_validate_config`。
"""
import re

# 方案里允许出现的键（顺序 = 界面展示顺序）
#
# v2.1.1.0：**账号与后缀**进方案了 ✓ —— 这是「多网络多账号」的关键：
#   宿舍用移动账号 + `@yd`、教学楼用校园账号 + 无尾缀，走到哪自动切到哪 ✓。
# ⚠️ 密码**仍然不进**配置 ✗：它只在 `password.txt`（或可选的 `password.<方案名>.txt`）里 ✓，
#   这是 v2.0.9.0 定下的隐私口径，没有放松 ✓。
# 兼容性：老方案里没有这两个键 → 切换时**不会**动它们 ✓（apply_to_config 只覆盖方案里有的键 ✓）。
PROFILE_KEYS = ("host", "port", "account", "suffix", "auto_check_interval_min",
                "network_guard_enabled", "guard_allowed_ssids", "guard_allowed_subnets")
PROFILE_LABELS = {
    "host": "认证网关",
    "port": "网关端口",
    "account": "账号",
    "suffix": "账号后缀（校内公共场合留空）",
    "auto_check_interval_min": "检查间隔",
    "network_guard_enabled": "网络位置守卫",
    "guard_allowed_ssids": "允许的 Wi-Fi 名",
    "guard_allowed_subnets": "允许的网段",
}
MAX_PROFILES = 12
MAX_NAME_LEN = 24
MAX_MATCH_SSIDS = 20
_BAD_CHARS = set('\\/:*?"<>|') | set("\r\n\t")


def normalize_name(name):
    """方案名归一化：压掉多余空白；不合法（空 / 超长 / 含路径字符）返回 ``""``。"""
    if not isinstance(name, str):
        return ""
    name = " ".join(name.split())
    if not name or len(name) > MAX_NAME_LEN:
        return ""
    if any(ch in _BAD_CHARS for ch in name):
        return ""
    return name


def _split_csv(text):
    """逗号分隔（中英文逗号都认）→ 去空白后的列表；非字符串按空处理。"""
    if not isinstance(text, str):
        return []
    return [x.strip() for x in text.replace("，", ",").split(",") if x.strip()]


def snapshot(cfg):
    """把「当前配置」拍成一份方案值（只取 PROFILE_KEYS 里存在的键）。"""
    cfg = cfg or {}
    return {k: cfg.get(k) for k in PROFILE_KEYS if k in cfg}


def normalize_match_ssids(raw):
    """方案的自动匹配 Wi-Fi 名：列表或逗号分隔字符串 → 归一化列表（去重、限量）。"""
    if isinstance(raw, str):
        items = _split_csv(raw)
    elif isinstance(raw, (list, tuple)):
        items = [str(x).strip() for x in raw if str(x).strip()]
    else:
        items = []
    out = []
    for item in items:
        if item not in out:
            out.append(item)
    return out[:MAX_MATCH_SSIDS]

def validate_values(values):
    """方案值形状校验（轻量）；返回错误列表。

    真正的把关在**切换时** —— 会把方案应用到配置副本上，交给注入的
    `_validate_config` 全量校验（见 `apply_to_config`），不通过就整体拒绝 ✗。
    """
    errors = []
    if not isinstance(values, dict):
        return ["方案内容必须是对象"]
    for k in values:
        if k not in PROFILE_KEYS:
            errors.append("方案里不认识的字段：{}（只允许 {}）".format(k, "、".join(PROFILE_KEYS)))
    if "host" in values and not isinstance(values["host"], str):
        errors.append("host 必须是字符串")
    # v2.1.1.0：账号与后缀进方案（后缀允许空串 = 校内直连 ✓）
    for key in ("account", "suffix"):
        if key in values and not isinstance(values[key], str):
            errors.append("{} 必须是字符串".format(key))
    if "port" in values:
        try:
            port = int(values["port"])
        except (TypeError, ValueError):
            errors.append("port 必须是整数")
        else:
            if not (1 <= port <= 65535):
                errors.append("port 必须在 1-65535 之间")
    if "auto_check_interval_min" in values:
        try:
            interval = int(values["auto_check_interval_min"])
        except (TypeError, ValueError):
            errors.append("auto_check_interval_min 必须是整数")
        else:
            if not (1 <= interval <= 1440):
                errors.append("auto_check_interval_min 必须在 1-1440 之间")
    if "network_guard_enabled" in values and not isinstance(values["network_guard_enabled"], bool):
        errors.append("network_guard_enabled 必须是布尔值")
    for key in ("guard_allowed_ssids", "guard_allowed_subnets"):
        if key in values and not isinstance(values[key], str):
            errors.append("{} 必须是字符串（逗号分隔）".format(key))
    return errors


def upsert(profiles, name, values, match_ssids=None):
    """新增 / 覆盖一个方案。返回 ``(新 profiles, 错误列表)``。"""
    name = normalize_name(name)
    if not name:
        return (profiles or {}), ["方案名不能为空（≤{} 字符，且不含 \\ / : * ? \" < > |）"
                                  .format(MAX_NAME_LEN)]
    errors = validate_values(values)
    if errors:
        return (profiles or {}), errors
    current = dict(profiles or {})
    if name not in current and len(current) >= MAX_PROFILES:
        return (profiles or {}), ["方案数已达上限 {} 个".format(MAX_PROFILES)]
    entry = {"values": {k: v for k, v in (values or {}).items() if k in PROFILE_KEYS}}
    matched = normalize_match_ssids(match_ssids if match_ssids is not None
                                    else (current.get(name) or {}).get("match_ssids"))
    if matched:
        entry["match_ssids"] = matched
    current[name] = entry
    return current, []


def delete(profiles, name):
    """删掉一个方案。返回 ``(新 profiles, 是否删掉了)``。"""
    current = dict(profiles or {})
    key = normalize_name(name) or (name if isinstance(name, str) else "")
    if key in current:
        del current[key]
        return current, True
    return current, False


def apply_to_config(cfg, profiles, name, validate_config=None):
    """把方案应用到配置上（**不改原对象**）。返回 ``(新 cfg, 错误列表)``。

    只覆盖方案里有的键，其它字段（账号、后缀、升级设置…）保持原样 ✓。
    ``validate_config`` 给定时，先拿合并结果跑一次全量校验，不通过就整体拒绝 ✗
    （这样"方案把配置改坏"在写入之前就被拦住）。
    """
    key = normalize_name(name)
    entry = (profiles or {}).get(key)
    if not isinstance(entry, dict):
        return cfg, ["没有这个方案：{!r}".format(name)]
    merged = dict(cfg or {})
    for k in PROFILE_KEYS:
        if k in (entry.get("values") or {}):
            merged[k] = entry["values"][k]
    merged["active_profile"] = key
    if validate_config is not None:
        errors = validate_config(merged) or []
        if errors:
            return cfg, ["方案「{}」会让配置非法：{}".format(key, "；".join(errors))]
    return merged, []


def pick_by_ssid(profiles, ssid):
    """按当前 Wi-Fi 名挑一个**自动方案**。返回方案名或 ``None``（大小写不敏感）。"""
    if not ssid or not isinstance(profiles, dict):
        return None
    want = str(ssid).strip().lower()
    if not want:
        return None
    for name, entry in profiles.items():
        if not isinstance(entry, dict):
            continue
        for item in entry.get("match_ssids") or []:
            if str(item).strip().lower() == want:
                return name
    return None


def describe(values):
    """一句话说清方案的关键点（界面副标题 / 日志用）。"""
    values = values or {}
    bits = []
    if values.get("network_guard_enabled"):
        ssids = _split_csv(values.get("guard_allowed_ssids"))
        subnets = _split_csv(values.get("guard_allowed_subnets"))
        guard = "守卫开"
        if ssids:
            guard += "（Wi-Fi " + "、".join(ssids[:2]) + ("…" if len(ssids) > 2 else "") + "）"
        elif subnets:
            guard += "（网段 " + "、".join(subnets[:2]) + ("…" if len(subnets) > 2 else "") + "）"
        bits.append(guard)
    else:
        bits.append("守卫关（到哪儿都尝试登录）")
    if values.get("auto_check_interval_min"):
        bits.append("每 {} 分钟检查".format(values["auto_check_interval_min"]))
    return " · ".join(bits)

# ============================================================
# 运行时引用注入（由 联网_service.py 在 main() 里调用）
# ============================================================
def _attach(*, logger=None, load_config=None, save_config=None, validate_config=None):
    """注入共享对象，与 web_api / auto_update / metrics 的 `_attach` 策略一致。"""
    g = globals()
    if logger is not None:
        g["logger"] = logger
    if load_config is not None:
        g["_load_config"] = load_config
    if save_config is not None:
        g["_save_config"] = save_config
    if validate_config is not None:
        g["_validate_config"] = validate_config
    return g


def list_profiles():
    """给界面 / 接口用：``{ok, active, auto_switch, count, items}``。"""
    cfg = {}
    loader = globals().get("_load_config")
    if loader is not None:
        try:
            cfg = loader() or {}
        except Exception:       # noqa: BLE001 —— 读配置失败也不该让接口 500
            cfg = {}
    profiles = cfg.get("profiles") or {}
    active = cfg.get("active_profile") or ""
    items = []
    for name in sorted(profiles, key=lambda n: (n != active, n)):
        entry = profiles.get(name) or {}
        values = entry.get("values") or {}
        items.append({
            "name": name,
            "active": name == active,
            "auto": bool(entry.get("match_ssids")),
            "match_ssids": entry.get("match_ssids") or [],
            "values": values,
            "desc": describe(values),
        })
    return {"ok": True, "active": active,
            "auto_switch": bool(cfg.get("profiles_auto_switch")),
            "count": len(items), "items": items}


def save_profile(name, values, match_ssids=None):
    """新建 / 覆盖一个方案并落盘。返回 ``{ok, error}``。"""
    loader = globals().get("_load_config")
    saver = globals().get("_save_config")
    if loader is None or saver is None:
        return {"ok": False, "error": "服务未就绪"}
    try:
        cfg = loader() or {}
    except Exception as exc:    # noqa: BLE001
        return {"ok": False, "error": "读取配置失败：{}".format(exc)}
    profiles, errors = upsert(cfg.get("profiles") or {}, name, values, match_ssids)
    if errors:
        return {"ok": False, "error": "；".join(errors)}
    cfg = dict(cfg)
    cfg["profiles"] = profiles
    saver(cfg)
    return {"ok": True, "name": normalize_name(name), "count": len(profiles)}


def activate(name):
    """应用一个方案并落盘。返回 ``{ok, error, applied, desc}``。

    校验走 `apply_to_config` + 注入的 `_validate_config`：**先验证再写盘** ✓，
    所以一个坏方案永远上不了线 ✗。
    """
    loader = globals().get("_load_config")
    saver = globals().get("_save_config")
    validator = globals().get("_validate_config")
    if loader is None or saver is None:
        return {"ok": False, "error": "服务未就绪"}
    try:
        cfg = loader() or {}
    except Exception as exc:    # noqa: BLE001
        return {"ok": False, "error": "读取配置失败：{}".format(exc)}
    merged, errors = apply_to_config(cfg, cfg.get("profiles") or {}, name, validator)
    if errors:
        return {"ok": False, "error": "；".join(errors)}
    saver(merged)
    values = snapshot(merged)
    log = globals().get("logger")
    if log is not None:
        log.info("已切换到配置方案「%s」：%s", normalize_name(name), describe(values))
    return {"ok": True, "name": normalize_name(name), "applied": values,
            "desc": describe(values)}


def remove(name):
    """删掉一个方案（删的是当前方案时只清空标记，**配置值保持不动** ✓）。"""
    loader = globals().get("_load_config")
    saver = globals().get("_save_config")
    if loader is None or saver is None:
        return {"ok": False, "error": "服务未就绪"}
    try:
        cfg = loader() or {}
    except Exception as exc:    # noqa: BLE001
        return {"ok": False, "error": "读取配置失败：{}".format(exc)}
    profiles, removed = delete(cfg.get("profiles") or {}, name)
    if not removed:
        return {"ok": False, "error": "没有这个方案"}
    cfg = dict(cfg)
    cfg["profiles"] = profiles
    if normalize_name(name) == (cfg.get("active_profile") or ""):
        cfg["active_profile"] = ""
    saver(cfg)
    return {"ok": True, "count": len(profiles)}


def auto_switch(ssid):
    """自动切换（每次检查时由 protocol 调用）。返回切到的方案名或 ``""``。

    三个条件全满足才动手：
        1. 总开关 `profiles_auto_switch` 打开（**默认关** —— 免得"我手动选的方案被系统改掉" ✗）；
        2. 有方案声明了 `match_ssids` 且命中当前 Wi-Fi 名（大小写不敏感）；
        3. 命中的方案不是当前方案。
    """
    loader = globals().get("_load_config")
    if loader is None or not ssid:
        return ""
    try:
        cfg = loader() or {}
    except Exception:           # noqa: BLE001
        return ""
    if not cfg.get("profiles_auto_switch"):
        return ""
    name = pick_by_ssid(cfg.get("profiles") or {}, ssid)
    if not name or name == (cfg.get("active_profile") or ""):
        return ""
    result = activate(name)
    if not result.get("ok"):
        log = globals().get("logger")
        if log is not None:
            log.warning("自动切换方案「%s」失败：%s", name, result.get("error"))
        return ""
    return name


