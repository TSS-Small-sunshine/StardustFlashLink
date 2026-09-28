# -*- coding: utf-8 -*-
"""metrics.py — 连接质量统计（B4 / v2.0.8.0）。

数据源就是**已有的日志**：`logs/campus_login.log` 里每个检查周期都留下了走向
（`开始检查 (reason=…)` → `已在线，无需登录` / `登录成功: …` / `登录失败: …` /
`等待 Ns 后 … 仍不可达` / 守卫跳过）。所以这里**不再养第二份状态文件** ——
服务重启不清零、升级不丢数据、用户删了日志也只是从零开始算。

产出（`GET /api/metrics?days=7`）：

    checks / online / relogin / fail / unreachable / skip / unknown   近 N 天的周期计数
    uptime_pct        —— (online + relogin) / (有效周期)，有效周期 = checks - skip
    avg_recover_ms    —— 重登周期的「开始检查 → 登录成功」耗时均值（登录本身要多久）
    avg_attempts      —— 网络可达探测平均第几次成功（>1 说明网不稳）
    avg_reach_ms      —— 可达探测平均耗时（v2.0.8.0 起才有这个字段，老日志为 null）
    latency_ms        —— 现场 TCP 连接耗时（「当前延迟」）
    series[]          —— 按天的计数（给前端画小柱图）
    last_relogin_at / last_fail_at / last_fail_msg / last_unreachable_at

设计：解析与统计**全是纯函数**（喂字符串就行，单测不碰磁盘、不碰网络）；
磁盘与网络访问只在 `read_log_tail()` 与 `measure_latency()` 这两个薄壳里。
"""
import os
import re
import socket
import time

LOG_NAME = "campus_login.log"
MAX_TAIL_BYTES = 4 * 1024 * 1024        # 只读尾部：真机约 1.3MB/9 天，4MB 够覆盖两周
DEFAULT_DAYS = 7
MAX_DAYS = 30
LATENCY_TIMEOUT = 3.0

_TS_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s*\[(\w+)\]\s?(.*)$")
_ATTEMPT_RE = re.compile(r"网络已可达（第 (\d+) 次尝试(?:，耗时 ([\d.]+)s)?）")

CYCLE_START = "开始检查 (reason="
EV_ONLINE = "已在线，无需登录"
EV_LOGIN_OK = "登录成功:"
EV_LOGIN_FAIL = "登录失败:"
EV_UNREACHABLE = "仍不可达"
EV_REACHABLE = "网络已可达（第 "
EV_SKIP = ("网络位置不在白名单", "密码未设置，跳过登录")

OUTCOME_ONLINE = "online"
OUTCOME_RELOGIN = "relogin"
OUTCOME_FAIL = "fail"
OUTCOME_UNREACHABLE = "unreachable"
OUTCOME_SKIP = "skip"
OUTCOME_UNKNOWN = "unknown"


def _parse_ts(text):
    """``'2026-09-28 07:28:05'`` → epoch 秒（本地时区）；解析不了返回 None。"""
    try:
        return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return None


def _day_of(epoch):
    """epoch → 'YYYY-MM-DD'。**必须容错**：Windows 上 `localtime()` 遇到越界/负值时间戳
    会抛 OSError（真跑测试时抓到：喂一个 1970 年附近的 now_epoch 就整段崩），
    而面板宁可少画一根柱子，也不该把状态页打成 500。"""
    try:
        return time.strftime("%Y-%m-%d", time.localtime(epoch))
    except (OSError, OverflowError, ValueError):
        return ""


def parse_log_lines(lines):
    """日志行 → 事件列表（dict：ts / epoch / level / text）。不认识的行直接跳过。

    日志格式会随版本长大（v2.0.8.0 就给「网络已可达」加了耗时）—— 解析器必须
    同时容忍**未知行**和**缺新字段的老行**，否则一次升级就能把面板清空。
    多行堆栈（升级日志里偶尔有）会因为没有时间戳前缀被自然跳过。
    """
    events = []
    for raw in lines:
        line = raw.rstrip("\r\n")
        m = _TS_RE.match(line)
        if not m:
            continue
        epoch = _parse_ts(m.group(1))
        if epoch is None:
            continue
        events.append({"ts": m.group(1), "epoch": epoch,
                       "level": m.group(2), "text": m.group(3)})
    return events

def group_cycles(events):
    """按「开始检查」把事件切成周期。返回 list[dict]。

    每个周期：``start_ts / start_epoch / end_epoch / reason / outcome / note / attempts /
    reach_sec``。``outcome`` ∈ {online, relogin, fail, unreachable, skip, unknown}。

    同一周期里的顺序是固定的（先「等待中」重试、再「已可达」、最后是登录走向），
    所以后面的行覆盖前面的判定是对的：`登录失败:` 之后紧跟的 `登录失败，进入退避：…`
    不会把 outcome 改坏（它不匹配任何分支），note 仍留第一次的可读原因。
    """
    cycles = []
    cur = None
    for ev in events:
        text = ev["text"]
        if text.startswith(CYCLE_START):
            if cur is not None:
                cycles.append(cur)
            reason = ""
            if "reason=" in text:
                reason = text.split("reason=", 1)[1].split(")", 1)[0]
            cur = {"start_ts": ev["ts"], "start_epoch": ev["epoch"], "end_epoch": ev["epoch"],
                   "reason": reason, "outcome": OUTCOME_UNKNOWN, "note": "",
                   "attempts": None, "reach_sec": None}
            continue
        if cur is None:
            continue
        cur["last_epoch"] = ev["epoch"]
        # end_epoch 只跟**判定行**走：周期与下一个周期之间还夹着别的日志
        # （升级、托盘、外部写入），拿"最后一行"当时长会算出几十分钟的假值 ✗
        if text.startswith(EV_ONLINE):
            cur["outcome"] = OUTCOME_ONLINE
            cur["end_epoch"] = ev["epoch"]
        elif text.startswith(EV_LOGIN_OK):
            cur["outcome"] = OUTCOME_RELOGIN
            cur["end_epoch"] = ev["epoch"]
            cur["note"] = text.split(":", 1)[1].strip()
        elif text.startswith(EV_LOGIN_FAIL):
            cur["outcome"] = OUTCOME_FAIL
            cur["end_epoch"] = ev["epoch"]
            cur["note"] = text.split(":", 1)[1].strip()
        elif EV_UNREACHABLE in text:
            cur["outcome"] = OUTCOME_UNREACHABLE
            cur["end_epoch"] = ev["epoch"]
            cur["note"] = text
        elif text.startswith(EV_REACHABLE):
            m = _ATTEMPT_RE.search(text)
            if m:
                try:
                    cur["attempts"] = int(m.group(1))
                except (TypeError, ValueError):
                    pass
                if m.group(2):
                    try:
                        cur["reach_sec"] = float(m.group(2))
                    except ValueError:
                        pass
        elif any(text.startswith(p) for p in EV_SKIP):
            cur["outcome"] = OUTCOME_SKIP
            cur["end_epoch"] = ev["epoch"]
            cur["note"] = text
    if cur is not None:
        cycles.append(cur)
    return cycles


def _avg(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / float(len(vals))


def summarize(cycles, now_epoch, days=DEFAULT_DAYS):
    """周期列表 → 指标 dict（纯函数）。窗口内没有周期时各项给 0 / None，不抛异常。

    ``days`` 可能来自查询串（`?days=abc`）—— 一律回退默认值再夹到 [1, MAX_DAYS]，
    绝不让一个坏参数把状态页打成 500（真跑测试时抓到过）。
    """
    try:
        days = int(days or DEFAULT_DAYS)
    except (TypeError, ValueError):
        days = DEFAULT_DAYS
    days = max(1, min(days, MAX_DAYS))
    since = now_epoch - days * 86400
    win = [c for c in cycles if c.get("end_epoch", 0) >= since]

    counts = {OUTCOME_ONLINE: 0, OUTCOME_RELOGIN: 0, OUTCOME_FAIL: 0,
              OUTCOME_UNREACHABLE: 0, OUTCOME_SKIP: 0, OUTCOME_UNKNOWN: 0}
    for c in win:
        counts[c.get("outcome", OUTCOME_UNKNOWN)] = counts.get(c.get("outcome"), 0) + 1

    checks = len(win)
    effective = checks - counts[OUTCOME_SKIP]          # 守卫跳过的周期不算「不健康」
    healthy = counts[OUTCOME_ONLINE] + counts[OUTCOME_RELOGIN]
    uptime_pct = round(healthy * 100.0 / effective, 1) if effective > 0 else None

    relogin_cycles = [c for c in win if c.get("outcome") == OUTCOME_RELOGIN]
    recover = [(c["end_epoch"] - c["start_epoch"]) * 1000.0 for c in relogin_cycles
               if c.get("end_epoch") and c.get("start_epoch")]
    avg_recover_ms = int(_avg(recover)) if recover else None

    attempts = [c.get("attempts") for c in win]
    reach_secs = [c.get("reach_sec") for c in win]
    avg_attempts = _avg(attempts)
    avg_reach_ms = int(_avg(reach_secs) * 1000) if _avg(reach_secs) is not None else None

    last_relogin = next((c for c in reversed(win) if c.get("outcome") == OUTCOME_RELOGIN), None)
    last_fail = next((c for c in reversed(win) if c.get("outcome") == OUTCOME_FAIL), None)
    last_unreach = next((c for c in reversed(win) if c.get("outcome") == OUTCOME_UNREACHABLE),
                        None)

    # 按天（只给窗口内的天，前端拿它画小柱图）
    series = []
    for i in range(days - 1, -1, -1):
        day = _day_of(now_epoch - i * 86400)
        day_cycles = [c for c in win if _day_of(c.get("end_epoch", 0)) == day]
        series.append({
            "date": day,
            "checks": len(day_cycles),
            "relogin": sum(1 for c in day_cycles if c.get("outcome") == OUTCOME_RELOGIN),
            "fail": sum(1 for c in day_cycles
                        if c.get("outcome") in (OUTCOME_FAIL, OUTCOME_UNREACHABLE)),
        })

    return {
        "days": days,
        "checks": checks,
        "online": counts[OUTCOME_ONLINE],
        "relogin": counts[OUTCOME_RELOGIN],
        "fail": counts[OUTCOME_FAIL],
        "unreachable": counts[OUTCOME_UNREACHABLE],
        "skip": counts[OUTCOME_SKIP],
        "unknown": counts[OUTCOME_UNKNOWN],
        "effective_checks": effective,
        "uptime_pct": uptime_pct,
        "avg_recover_ms": avg_recover_ms,
        "avg_attempts": round(avg_attempts, 2) if avg_attempts is not None else None,
        "avg_reach_ms": avg_reach_ms,
        "last_relogin_at": (last_relogin or {}).get("start_ts"),
        "last_fail_at": (last_fail or {}).get("start_ts"),
        "last_fail_msg": (last_fail or {}).get("note") or None,
        "last_unreachable_at": (last_unreach.get("start_ts") if last_unreach else None),
        "series": series,
    }

# ============================================================
# 薄壳：磁盘 / 网络（上层只通过 build_metrics / api_get_metrics 用它们）
# ============================================================
def read_log_tail(path, max_bytes=MAX_TAIL_BYTES):
    """读日志尾部，返回行列表。文件不存在 / 读不了 → ``[]``（面板留空，不报错）。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    try:
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            data = f.read()
    except OSError:
        return []
    text = data.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]        # 从中间开始读 → 首行多半被切断，丢掉
    return lines


def measure_latency(host, port, timeout=LATENCY_TIMEOUT):
    """到 ``host:port`` 的 TCP 连接耗时（毫秒 int）；失败返回 None。

    「当前延迟」量的是 TCP 握手，不是 ICMP ping：校园网 / Windows 防火墙常禁 ping，
    而 gateway:80 是服务本来就要连的地址 —— 量它才有意义，也才稳定。
    """
    if not host or not port:
        return None
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None
    t0 = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return int((time.time() - t0) * 1000)
    except OSError:
        return None


def _archive_of(path):
    """`x.log` → `x.log.1`。我们自己没轮转 campus_login.log，但被外部轮转过的机器上可能有。"""
    return path + ".1"


def collect_lines(log_path, max_bytes=MAX_TAIL_BYTES):
    """日志行 = 归档 `.1`（更老）+ 当前文件；缺哪个都不会炸。"""
    if not log_path:
        return []
    return read_log_tail(_archive_of(log_path), max_bytes) + read_log_tail(log_path, max_bytes)


def build_metrics(days=DEFAULT_DAYS, now_epoch=None, log_path=None, latency=None,
                  latency_host=None, latency_port=None):
    """读日志 → 解析 → 统计 → 组装响应。外部依赖全可覆盖（方便单测与排障）。"""
    now_epoch = now_epoch or time.time()
    data = summarize(group_cycles(parse_log_lines(collect_lines(log_path or ""))),
                     now_epoch, days)
    if latency is None:
        latency = measure_latency(latency_host, latency_port)
    data["latency_ms"] = latency
    data["log_file"] = log_path or ""
    data["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_epoch))
    return data


# ============================================================
# 运行时引用注入（由 联网_service.py 在 import 后调用）
# ============================================================
def _attach(*, log_dir="", base_dir="", load_config=None, logger=None):
    """注入共享对象，与 web_api / auto_update / eula 的 `_attach` 策略一致。"""
    g = globals()
    g["LOG_DIR"] = log_dir or base_dir or ""
    g["BASE_DIR"] = base_dir or ""
    if load_config is not None:
        g["_load_config"] = load_config
    if logger is not None:
        g["logger"] = logger
    return g


def api_get_metrics(days=DEFAULT_DAYS):
    """给 Web 层的一行入口：按当前配置定位日志 → 出指标。

    刻意**不抛异常**：面板是「锦上添花」的东西，配置读不到、日志被删了都只应该显示空白，
    而不是把状态页打成 500。
    """
    cfg = {}
    loader = globals().get("_load_config")
    if loader is not None:
        try:
            cfg = loader() or {}
        except Exception:        # noqa: BLE001 —— 配置读失败不该让面板整个 500
            cfg = {}
    log_dir = globals().get("LOG_DIR") or ""
    path = os.path.join(log_dir, LOG_NAME) if log_dir else ""
    return build_metrics(days=days, log_path=path,
                         latency_host=cfg.get("host"), latency_port=cfg.get("port"))


