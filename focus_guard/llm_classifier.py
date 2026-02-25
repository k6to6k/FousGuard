"""
FocusGuard 大模型意图评判引擎 (llm_classifier.py)

职责：
- 通过 SiliconFlow 托管的 deepseek-ai/DeepSeek-V3 模型，对当前行为（本地应用或网页）与用户专注目标之间的关联度进行智能审计。
- 仅依赖标准库（urllib.request 与 json），方便在任何环境下部署。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List


API_URL = "https://api.siliconflow.cn/v1/chat/completions"
# 注意：真实使用时应将密钥放入环境变量或独立配置文件中，此处为演示按产品文档直接写入。
API_KEY = "sk-ekvvkobtfevbbcdybnzsjldsxhfdcbpdffuwqlloyxxmbgts"

# 简单的本地决策缓存，避免频繁命中同一页面时重复请求云端
_DECISION_CACHE: Dict[str, bool] = {}


def _build_messages(
    focus_target: str,
    process_name: str,
    window_title: str,
    page_url: str,
) -> List[Dict[str, str]]:
    """
    构造发往大模型的对话 messages。

    - system：定义为全局操作系统行为审计专家，只允许输出 ALLOW 或 BLOCK。
    - user：包含专注目标、进程名、窗口标题与 URL（若有）。
    """
    system_prompt = (
        "你现在是一个全局操作系统行为审计专家。用户可能在访问网页（提供 URL），"
        "也可能在使用本地桌面软件（仅提供进程名和窗口标题）。请综合判断当前行为是否与专注目标高度相关。"
        "如果是学习/工作必需，返回 'ALLOW'。如果属于娱乐、摸鱼或明显偏离目标，立刻返回 'BLOCK'。"
        "除了这两个单词，绝对不许输出任何其他字符！"
        "【特权豁免】如果该行为对应的是搜索引擎首页（如 Google、百度）、"
        "内容网站的首页/搜索页（如 B 站首页、知乎首页、GitHub 主页等导航性质页面），"
        "或系统默认设置页、软件的默认启动大厅等无明确语义的导航页面，请务必返回 'ALLOW'，"
        "因为用户需要通过这些入口来搜索或打开学习资料。只有当用户明确进入了与目标无关的娱乐/闲聊具体内容页时，才返回 'BLOCK'。"
    )

    user_prompt = (
        f"专注目标：{focus_target}。"
        f"进程名：{process_name}。"
        f"窗口标题：{window_title}。"
        f"URL：{page_url}。"
        "请判定："
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def evaluate_intent(
    focus_target: str,
    process_name: str,
    window_title: str,
    page_url: str = "",
) -> Any:
    """
    使用 LLM 评估“当前行为”（本地应用或网页）是否偏离当前专注目标。

    参数：
    - focus_target: 用户当前设定的专注目标（如 "复习数据库"）。
    - process_name: 当前前台进程名（如 "chrome.exe" 或 "steam.exe"）。
    - window_title: 当前窗口标题或标签页标题。
    - page_url: 若为浏览器场景，则为当前标签页 URL；本地应用可为空字符串。

    返回：
    - True   : LLM 判定包含 "BLOCK" → 需要拦截。
    - False  : LLM 明确判定无需拦截 → 放行。
    - None   : 网络/解析/结构异常，未能给出结论（由调用方决定是否降级到静态规则）。
    """
    # 1. 本地缓存命中：同一专注目标 + 同一进程 + 同一窗口标题 + 同一 URL 直接复用决策
    cache_key = f"{focus_target}|{process_name}|{window_title}|{page_url}"
    if cache_key in _DECISION_CACHE:
        print(f"[FocusGuard] 命中 LLM 缓存，0延迟响应: {window_title} ({process_name})")
        return _DECISION_CACHE[cache_key]

    messages = _build_messages(focus_target, process_name, window_title, page_url)

    payload: Dict[str, Any] = {
        "model": "deepseek-ai/DeepSeek-V3",
        "messages": messages,
        "temperature": 0.0,
        "max_tokens": 10,
    }

    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(API_URL, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {API_KEY}")
    req.add_header("Content-Type", "application/json")

    try:
        # 显式设置超时时间，防止云端推理偶发卡顿拖垮本地监控循环
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_bytes = resp.read()
    except urllib.error.URLError as exc:
        print(f"[LLM 分类引擎] 请求失败（URLError）：{exc}")
        return None
    except Exception as exc:
        print(f"[LLM 分类引擎] 请求失败（异常）：{exc}")
        return None

    try:
        resp_json = json.loads(resp_bytes.decode("utf-8"))
    except Exception as exc:
        print(f"[LLM 分类引擎] 响应 JSON 解析失败：{exc}")
        return None

    # 兼容 OpenAI 风格的 chat.completions 返回结构
    content = ""
    try:
        choices = resp_json.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            content = str(message.get("content") or "")
    except Exception as exc:
        print(f"[LLM 分类引擎] 解析模型回复时出错：{exc}")
        return None

    normalized = content.strip().upper()
    # 极简决策：只要包含 BLOCK 就视为需要拦截
    is_block = "BLOCK" in normalized

    # 简单容量控制，防止缓存无限增长
    if len(_DECISION_CACHE) > 200:
        _DECISION_CACHE.clear()
    _DECISION_CACHE[cache_key] = is_block

    return is_block


if __name__ == "__main__":
    # 本地简单自测：模拟两条网页访问记录
    print("=== LLM 分类引擎本地自测 ===")

    # 用例一：浏览器学习相关的技术内容（期望多半为 ALLOW）
    target1 = "复习算法"
    proc1 = "chrome.exe"
    title1 = "哔哩哔哩-搜广推经典召回算法解析"
    url1 = "https://www.bilibili.com/video/xxx"

    # 用例二：浏览器明显的娱乐向内容（期望多半为 BLOCK）
    target2 = "复习算法"
    proc2 = "chrome.exe"
    title2 = "漫威电影宇宙时间线盘点"
    url2 = "https://www.yuque.com/xxx"

    block1 = evaluate_intent(target1, proc1, title1, url1)
    block2 = evaluate_intent(target2, proc2, title2, url2)

    print(f"[用例一] 是否需要拦截: {block1}")
    print(f"[用例二] 是否需要拦截: {block2}")

    # 模拟监控循环在 1.5 秒后再次扫到同一个网页/应用，验证缓存是否生效
    print("\n=== 重复命中用例一，验证缓存 ===")
    block3 = evaluate_intent(target1, proc1, title1, url1)
    print(f"[用例一-重复] 是否需要拦截: {block3}")

