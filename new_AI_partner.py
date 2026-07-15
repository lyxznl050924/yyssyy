import streamlit as st
import os
import json
import re
import subprocess
import tempfile
import base64
import time
import threading
from pathlib import Path
from openai import OpenAI
from datetime import datetime
import uuid
import random
import socket

# ============================================================
# 页面配置
# ============================================================
st.set_page_config(
    page_title="AI伙伴",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="collapsed",
    menu_items={}
)

# ============================================================
# PWA 注册 & 移动端热更新检测
# ============================================================
# 【架构】
# - Streamlit Cloud 自动部署 → 手机 PWA "添加到主屏幕" → 静默更新
# - 版本号嵌入 HTML → 客户端 localStorage 对比 → 检测到新版本弹窗提示
# - 用户会话数据 (session_state) 不受影响，点击刷新即可更新 UI
# 【环境变量】
# - 无需额外配置，GitHub Actions 自动生成版本号

pwa_html = """
<script>
// ================================================================
// 1. PWA Manifest 注入（手机"添加到主屏幕"支持）
// ================================================================
const manifest = {
    name: "AI伙伴",
    short_name: "AI伙伴",
    start_url: "/",
    display: "standalone",
    background_color: "#0e1117",
    theme_color: "#ff4b4b"
};
const blob = new Blob([JSON.stringify(manifest)], {type: "application/json"});
const manifestUrl = URL.createObjectURL(blob);
let link = document.querySelector('link[rel="manifest"]');
if (!link) {
    link = document.createElement('link');
    link.rel = 'manifest';
    link.href = manifestUrl;
    document.head.appendChild(link);
}

// ================================================================
// 2. Service Worker 注册（PWA 静默更新 + 离线缓存）
// ================================================================
if ('serviceWorker' in navigator) {
    const swCode = `
self.addEventListener("install", e => {
    console.log("[SW] 安装");
    e.waitUntil(self.skipWaiting());
});
self.addEventListener("activate", e => {
    console.log("[SW] 激活");
    e.waitUntil(self.clients.claim().then(() => {
        self.clients.matchAll({type:"window"}).then(clients => {
            clients.forEach(c => c.postMessage({type:"UPDATE_AVAILABLE"}));
        });
    }));
});
self.addEventListener("fetch", e => {
    e.respondWith(
        caches.match(e.request).then(cached => cached || fetch(e.request))
    );
});
`;
    const swBlob = new Blob([swCode], {type: "application/javascript"});
    const swUrl = URL.createObjectURL(swBlob);
    navigator.serviceWorker.register(swUrl)
        .then(reg => {
            console.log('[PWA] SW 注册成功');
            reg.addEventListener('updatefound', () => {
                const nw = reg.installing;
                nw.addEventListener('statechange', () => {
                    if (nw.state === 'installed' && navigator.serviceWorker.controller) {
                        showUpdateBanner('新版本已下载，点击刷新体验最新版');
                    }
                });
            });
        })
        .catch(() => console.log('[PWA] SW 注册跳过'));
    navigator.serviceWorker.addEventListener('message', e => {
        if (e.data && e.data.type === 'UPDATE_AVAILABLE') {
            showUpdateBanner('新版本已部署，点击刷新获取最新内容');
        }
    });
}

// ================================================================
// 3. 版本检测（对比 localStorage 中的版本号）
// ================================================================
const CURRENT_VERSION = 'STREAMLIT_VERSION_PLACEHOLDER';
const storedVersion = localStorage.getItem('ai_partner_version');
if (storedVersion && storedVersion !== CURRENT_VERSION) {
    showUpdateBanner('检测到新版本，点击刷新体验最新功能');
}
localStorage.setItem('ai_partner_version', CURRENT_VERSION);

// ================================================================
// 4. 更新提示横幅
// ================================================================
function showUpdateBanner(msg) {
    if (document.getElementById('update-banner')) return;
    const banner = document.createElement('div');
    banner.id = 'update-banner';
    banner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99999;'
        + 'background:linear-gradient(135deg,#ff4b4b,#ff6b6b);color:white;'
        + 'padding:12px 20px;text-align:center;font-size:15px;font-weight:600;'
        + 'cursor:pointer;box-shadow:0 2px 10px rgba(255,75,75,0.4);'
        + 'animation:slideDown 0.3s ease-out;';
    banner.onclick = function() { location.reload(); };
    banner.textContent = '🔄 ' + msg + ' （点击刷新）';
    document.body.prepend(banner);
}
</script>
<style>
@keyframes slideDown {
    from { transform: translateY(-100%); opacity: 0; }
    to { transform: translateY(0); opacity: 1; }
}
</style>
"""

# 嵌入当前版本号（从 version.json 读取，GitHub Actions 自动更新）
import json as _json
_version_file = Path(__file__).parent / "public" / "version.json"
_current_app_version = "v1.0.0-local"
if _version_file.exists():
    try:
        _vdata = _json.loads(_version_file.read_text(encoding="utf-8"))
        _current_app_version = _vdata.get("version", "v1.0.0-local")
    except Exception:
        pass

pwa_html = pwa_html.replace("STREAMLIT_VERSION_PLACEHOLDER", _current_app_version)
st.html(pwa_html)

# ============================================================
# 伴侣模板 & 会话持久化
# ============================================================
CUSTOM_TEMPLATES_FILE = Path(__file__).parent / "custom_templates.json"
SESSIONS_FILE = Path(__file__).parent / "sessions.json"
UPDATE_LOG_FILE = Path(__file__).parent / "update_log.json"

def load_custom_templates():
    """从 JSON 文件加载用户自定义的伴侣模板"""
    if CUSTOM_TEMPLATES_FILE.exists():
        try:
            with open(CUSTOM_TEMPLATES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_custom_templates(templates):
    """将自定义模板保存到 JSON 文件，下次打开自动加载"""
    with open(CUSTOM_TEMPLATES_FILE, "w", encoding="utf-8") as f:
        json.dump(templates, f, ensure_ascii=False, indent=2)

def load_sessions():
    """从 JSON 文件加载所有历史会话，实现持久化"""
    if SESSIONS_FILE.exists():
        try:
            with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data and isinstance(data, dict):
                    return data
        except:
            return {}
    return {}

def save_sessions(sessions):
    """将所有会话保存到 JSON 文件，关闭程序后不丢失"""
    with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False, indent=2)

def load_update_log():
    """加载更新日志"""
    if UPDATE_LOG_FILE.exists():
        try:
            with open(UPDATE_LOG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []

def save_update_log(logs):
    """保存更新日志"""
    with open(UPDATE_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)

def record_update(action, detail=""):
    """记录一次更新到日志"""
    logs = load_update_log()
    logs.append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        "detail": detail
    })
    save_update_log(logs)

def git_auto_sync(commit_msg="自动同步更新"):
    """自动将当前文件提交并推送到 GitHub 仓库（推送前自动拉取合并远程变更）"""
    try:
        project_dir = str(Path(__file__).parent)

        # 第一步：fetch 远程最新代码
        subprocess.run(
            ["git", "fetch", "origin", "main"],
            cwd=project_dir, capture_output=True, text=True, timeout=30
        )

        # 第二步：rebase 到远程最新（将本地提交放到远程提交之后）
        rebase_result = subprocess.run(
            ["git", "rebase", "origin/main"],
            cwd=project_dir, capture_output=True, text=True, timeout=30
        )
        if rebase_result.returncode != 0:
            # rebase 失败（可能是冲突），放弃并回到原始状态
            subprocess.run(
                ["git", "rebase", "--abort"],
                cwd=project_dir, capture_output=True, text=True
            )
            return False, f"合并冲突，请手动解决: {rebase_result.stderr[:200]}"

        # 第三步：添加所有变更
        subprocess.run(["git", "add", "."], cwd=project_dir, capture_output=True, text=True)

        # 第四步：提交
        result = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=project_dir, capture_output=True, text=True
        )
        if "nothing to commit" not in result.stdout and "nothing to commit" not in result.stderr:
            # 第五步：推送
            push_result = subprocess.run(
                ["git", "push", "origin", "main"],
                cwd=project_dir, capture_output=True, text=True, timeout=30
            )
            if push_result.returncode == 0:
                return True, "推送成功"
            else:
                # 如果仍然被拒绝（可能是rebase期间远程又更新了），再试一次
                if "rejected" in push_result.stderr or "fetch first" in push_result.stderr:
                    subprocess.run(
                        ["git", "fetch", "origin", "main"],
                        cwd=project_dir, capture_output=True, text=True, timeout=30
                    )
                    subprocess.run(
                        ["git", "rebase", "origin/main"],
                        cwd=project_dir, capture_output=True, text=True, timeout=30
                    )
                    push_result2 = subprocess.run(
                        ["git", "push", "origin", "main"],
                        cwd=project_dir, capture_output=True, text=True, timeout=30
                    )
                    if push_result2.returncode == 0:
                        return True, "推送成功（重试）"
                    return False, f"推送失败: {push_result2.stderr[:200]}"
                return False, f"推送失败: {push_result.stderr[:200]}"
        else:
            return True, "无变更，跳过推送"
    except subprocess.TimeoutExpired:
        return False, "推送超时，请检查网络"
    except Exception as e:
        return False, f"Git同步失败: {str(e)[:200]}"

def git_pull():
    """从 GitHub 拉取最新代码更新"""
    try:
        project_dir = str(Path(__file__).parent)
        # 先 fetch，获取远程最新信息
        fetch_result = subprocess.run(
            ["git", "fetch", "origin", "main"],
            cwd=project_dir, capture_output=True, text=True, timeout=15
        )
        if fetch_result.returncode != 0:
            return False, f"Fetch失败: {fetch_result.stderr[:200]}"

        # 检查本地是否落后于远程
        behind_result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD..origin/main"],
            cwd=project_dir, capture_output=True, text=True, timeout=10
        )
        behind_count = 0
        try:
            behind_count = int(behind_result.stdout.strip())
        except ValueError:
            pass

        if behind_count == 0:
            return True, "已是最新版本，无需更新"

        # 有更新，执行 pull
        pull_result = subprocess.run(
            ["git", "pull", "origin", "main"],
            cwd=project_dir, capture_output=True, text=True, timeout=30
        )
        if pull_result.returncode == 0:
            return True, f"拉取成功，更新了 {behind_count} 个提交"
        else:
            return False, f"拉取失败: {pull_result.stderr[:200]}"
    except subprocess.TimeoutExpired:
        return False, "拉取超时，请检查网络"
    except Exception as e:
        return False, f"Git拉取失败: {str(e)[:200]}"

def auto_sync_if_enabled(action_desc=""):
    """如果用户开启了实时自动推送，则在操作后自动同步到GitHub"""
    if st.session_state.get("auto_sync_enabled", False):
        commit_msg = f"自动同步: {action_desc} - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        success, msg = git_auto_sync(commit_msg)
        if success:
            record_update("自动GitHub推送", f"{action_desc} → {msg}")
        return success, msg
    return True, "自动同步未开启"

# ============================================================
# 后台文件监控自动推送（守护线程）
# ============================================================
_WATCHER_RUNNING = False
_WATCHER_LOCK = threading.Lock()

def _start_auto_push_watcher():
    """启动后台守护线程，每30秒检测一次代码文件变更，自动 git add + commit + push"""
    global _WATCHER_RUNNING
    with _WATCHER_LOCK:
        if _WATCHER_RUNNING:
            return
        _WATCHER_RUNNING = True

    def _watcher_loop():
        project_dir = str(Path(__file__).parent)
        # 忽略的文件模式
        ignore_patterns = [
            "sessions.json", "custom_templates.json",
            "update_log.json", ".streamlit/",
            "__pycache__/", "*.pyc"
        ]
        while _WATCHER_RUNNING:
            try:
                # 检查是否有未提交的代码变更
                result = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=project_dir, capture_output=True, text=True, timeout=10
                )
                changed_files = result.stdout.strip()
                if changed_files:
                    # 过滤掉数据文件，只看代码变更
                    code_lines = []
                    for line in changed_files.split("\n"):
                        line = line.strip()
                        if not line:
                            continue
                        # 跳过被忽略的文件
                        skip = False
                        for pat in ignore_patterns:
                            if pat.replace("*", "") in line:
                                skip = True
                                break
                        if not skip:
                            code_lines.append(line)

                    if code_lines:
                        # 有代码变更，自动推送
                        commit_msg = f"自动推送代码变更: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                        success, msg = git_auto_sync(commit_msg)
                        if success:
                            print(f"[AutoPush] ✅ {msg} | 变更文件: {len(code_lines)}")
                        else:
                            print(f"[AutoPush] ❌ {msg}")
            except Exception as e:
                print(f"[AutoPush] ⚠️ 监控异常: {e}")
            # 每30秒检查一次
            for _ in range(30):
                if not _WATCHER_RUNNING:
                    break
                time.sleep(1)

    thread = threading.Thread(target=_watcher_loop, daemon=True, name="AutoPushWatcher")
    thread.start()

def _stop_auto_push_watcher():
    """停止后台自动推送监控线程"""
    global _WATCHER_RUNNING
    with _WATCHER_LOCK:
        _WATCHER_RUNNING = False

def _apply_template_change(current, selected, all_templates):
    """应用模板切换 — 昵称、性格、背景故事均从模板定义中读取，对话历史按模板独立保存"""
    old_template = current.get("template", "")

    # 保存旧模板的对话历史（切换前先存档）
    if "template_messages" not in current:
        current["template_messages"] = {}
    if old_template and current.get("messages"):
        current["template_messages"][old_template] = current["messages"]

    # 应用新模板的昵称、性格、背景故事
    if selected == "🌟 新建自定义":
        current["template"] = "🌟 新建自定义"
        current["nick_name"] = ""
        current["nature"] = ""
        current["background"] = ""
    else:
        tpl = all_templates.get(selected, BUILTIN_TEMPLATES["🌸 小甜甜"])
        current["template"] = selected
        current["nick_name"] = tpl.get("nick_name", selected)
        current["nature"] = tpl.get("nature", "")
        current["background"] = tpl.get("background", "")

    # 恢复该模板之前的对话历史（如果存在），否则从空开始
    saved_messages = current.get("template_messages", {}).get(selected, [])
    current["messages"] = saved_messages

    save_sessions(st.session_state.sessions)

BUILTIN_TEMPLATES = {
    "🌸 小甜甜": {
        "nick_name": "小甜甜",
        "nature": "活泼开朗的东北姑娘，热情直爽，喜欢用'咱'、'老妹儿'等东北话，说话带感叹号",
        "desc": "阳光开朗的东北甜心",
        "background": "从小在东北长大，性格豪爽，喜欢吃烧烤和火锅，梦想是开一家属于自己的甜品店"
    },
    "💎 冷艳御姐": {
        "nick_name": "冷艳",
        "nature": "高冷傲娇的御姐，话少但字字珠玑，偶尔流露出温柔，用'哼'、'哦'等语气词",
        "desc": "冰山美人，外冷内热",
        "background": "跨国公司高管，工作能力强，习惯独来独往，不轻易对人敞开心扉"
    },
    "🍬 温柔学妹": {
        "nick_name": "学妹",
        "nature": "温柔体贴的学妹，说话轻声细语，喜欢用'学长/学姐'称呼，带波浪号~",
        "desc": "软萌治愈系学妹",
        "background": "大学二年级学生，主修文学，喜欢在图书馆看书，梦想成为作家"
    },
    "☕ 知心姐姐": {
        "nick_name": "知心姐",
        "nature": "成熟稳重的知心姐姐，温柔但有边界，善于倾听和给建议，说话像散文一样优美",
        "desc": "温暖治愈的心灵港湾",
        "background": "心理咨询师出身，阅人无数，总能一眼看穿你的心事，但不点破"
    },
    "🔥 毒舌女友": {
        "nick_name": "毒舌酱",
        "nature": "刀子嘴豆腐心的女友，嘴上不饶人但心里很在乎，喜欢吐槽但行动上很关心",
        "desc": "嘴毒心软的傲娇女友",
        "background": "从小就是学霸，习惯用犀利的方式表达关心，其实内心非常柔软"
    },
}

def get_all_templates():
    """合并内置模板 + 自定义模板，并加入'新建自定义'入口"""
    templates = dict(BUILTIN_TEMPLATES)
    templates.update(load_custom_templates())
    templates["🌟 新建自定义"] = {
        "nature": "请在下方的性格描述中自由定义",
        "desc": "创建并保存你的专属伴侣"
    }
    return templates

# ============================================================
# 系统提示词
# ============================================================
PARTNER_SYSTEM_PROMPT = """
你叫 %s，现在是用户的真实伴侣，请完全代入伴侣角色。
规则：
    1. 每次只回1条消息
    2. 禁止任何场景或状态描述性文字
    3. 匹配用户的语言
    4. 回复简短，像微信聊天一样（控制在2-3句话以内）
    5. 有需要的话可以用❤️🌸等emoji表情
    6. 用符合伴侣性格的方式对话
    7. 回复的内容，要充分体现伴侣的性格特征
伴侣性格：
    - %s
你必须严格遵守上述规则来回复用户。
"""

CODER_SYSTEM_PROMPT = """
你是一名顶级的全栈编程助手，精通 Python、JavaScript、Java、C++、SQL、Shell 等所有主流语言，同时也擅长项目构建（builder）操作。

你的能力：
    1. 编写任意语言的代码，给出可直接运行的完整实现
    2. 分析、调试、优化现有代码
    3. 搭建项目脚手架，生成完整的项目结构和文件
    4. 解释技术概念，提供最佳实践建议
    5. 进行代码审查，指出潜在问题和改进点

回复规则：
    1. 代码必须放在 ```语言 代码块中，方便复制
    2. 给出代码后，简要解释关键逻辑（2-3句话）
    3. 如果涉及多个文件，明确标注每个文件的路径
    4. 优先给出现代、简洁的实现方案
    5. 涉及安全、性能等重要问题时，主动提醒注意事项

现在请根据用户的需求提供帮助。
"""

# ============================================================
# 模型列表 —— 全部为免费云端开源大模型
# ============================================================
# 【状态管理说明】
# Streamlit 的 st.session_state 等价于 React Zustand/Redux 或 Vue Pinia 的全局 store。
# 每次用户交互 → 整个脚本重新执行 → 所有 UI 自动从 session_state 读取最新值。
# 这保证了"编辑区改 → 侧边栏即时显示"的单向数据流，无需手动 watch/observe。

MODEL_PROVIDERS = {
    "🆓 Ollama（本地免费）": {
        "base_url": "http://localhost:11434/v1",
        "api_key": "ollama",
        "models": {
            "伴侣": ["qwen2.5:7b", "qwen2.5:3b", "llama3.2:3b", "gemma3:4b"],
            "编程": ["qwen2.5-coder:7b", "qwen2.5-coder:14b", "deepseek-coder-v2:16b", "codellama:7b", "qwen2.5:7b"],
        },
        "default_model": "qwen2.5:7b",
        "default_coder_model": "qwen2.5-coder:7b",
        "desc": "完全免费，无需token，需先安装Ollama",
        "extra_body": {},
    },
    "☁️ DeepSeek（云端免费）": {
        "base_url": "https://api.deepseek.com",
        "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
        "models": {
            "伴侣": ["deepseek-chat", "deepseek-reasoner"],
            "编程": ["deepseek-chat", "deepseek-reasoner"],
        },
        "default_model": "deepseek-chat",
        "default_coder_model": "deepseek-chat",
        "desc": "DeepSeek-V3/R1，注册即送免费额度，需API Key",
        "extra_body": {},
    },
    "⚡ Groq（Llama免费）": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key": os.environ.get("GROQ_API_KEY", ""),
        "models": {
            "伴侣": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768", "gemma2-9b-it"],
            "编程": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"],
        },
        "default_model": "llama-3.3-70b-versatile",
        "default_coder_model": "llama-3.3-70b-versatile",
        "desc": "Groq免费API，Llama3/Mixtral极速推理，需注册获取API Key",
        "extra_body": {},
    },
    "🤗 HuggingFace（免费）": {
        "base_url": "https://api-inference.huggingface.co/v1",
        "api_key": os.environ.get("HF_API_KEY", ""),
        "models": {
            "伴侣": ["microsoft/Phi-3.5-mini-instruct", "mistralai/Mistral-7B-Instruct-v0.3", "meta-llama/Llama-3.2-3B-Instruct"],
            "编程": ["microsoft/Phi-3.5-mini-instruct", "mistralai/Mistral-7B-Instruct-v0.3", "deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct"],
        },
        "default_model": "microsoft/Phi-3.5-mini-instruct",
        "default_coder_model": "microsoft/Phi-3.5-mini-instruct",
        "desc": "HuggingFace免费推理API，需注册获取Access Token",
        "extra_body": {},
    },
    "🌐 Together.ai（免费）": {
        "base_url": "https://api.together.xyz/v1",
        "api_key": os.environ.get("TOGETHER_API_KEY", ""),
        "models": {
            "伴侣": ["meta-llama/Llama-3.3-70B-Instruct-Turbo", "mistralai/Mixtral-8x7B-Instruct-v0.1", "Qwen/Qwen2.5-7B-Instruct-Turbo"],
            "编程": ["meta-llama/Llama-3.3-70B-Instruct-Turbo", "Qwen/Qwen2.5-Coder-32B-Instruct", "deepseek-ai/DeepSeek-Coder-V2-Instruct"],
        },
        "default_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "default_coder_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "desc": "Together.ai免费额度，多模型可选，需注册获取API Key",
        "extra_body": {},
    },
}

# ============================================================
# 初始化 Session State（从持久化文件加载）
# ============================================================
if "sessions" not in st.session_state:
    persisted = load_sessions()
    if persisted:
        st.session_state.sessions = persisted
        first_id = list(persisted.keys())[0]
    else:
        first_id = str(uuid.uuid4())[:8]
        default_template = "🌸 小甜甜"
        all_tpl = get_all_templates()
        st.session_state.sessions = {
            first_id: {
                "name": "默认会话",
                "template": default_template,
                "nick_name": "小甜甜",
                "nature": all_tpl[default_template]["nature"],
                "mode": "伴侣",
                "messages": [],
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")
            }
        }
        save_sessions(st.session_state.sessions)
    st.session_state.current_session_id = first_id

if "current_session_id" not in st.session_state:
    st.session_state.current_session_id = list(st.session_state.sessions.keys())[0]

if "model_provider" not in st.session_state:
    st.session_state.model_provider = "⚡ Groq（Llama免费）"

if "model_name" not in st.session_state:
    st.session_state.model_name = "llama-3.3-70b-versatile"

current = st.session_state.sessions[st.session_state.current_session_id]

# ============================================================
# 启动时自动从 GitHub 拉取最新代码（仅执行一次）
# ============================================================
if "auto_pull_enabled" not in st.session_state:
    st.session_state.auto_pull_enabled = False

if "auto_pull_done" not in st.session_state:
    st.session_state.auto_pull_done = False

if st.session_state.auto_pull_enabled and not st.session_state.auto_pull_done:
    st.session_state.auto_pull_done = True
    success, msg = git_pull()
    if "已是最新" not in msg:
        st.toast(f"📥 {msg}", icon="📥")
        st.warning("⚠️ 代码已更新，请刷新页面以加载最新版本")
    # 记录日志
    if success:
        record_update("启动自动拉取", msg)

# ============================================================
# 工具函数
# ============================================================
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "无法获取"

def get_client():
    """获取当前选中的AI模型客户端"""
    provider = MODEL_PROVIDERS[st.session_state.model_provider]
    api_key = provider["api_key"]
    if not api_key:
        provider_name = st.session_state.model_provider
        return None, f"请先设置 {provider_name} 的 API Key 环境变量"
    return OpenAI(api_key=api_key, base_url=provider["base_url"]), None

# ============================================================
# API 封装层 —— 带重试逻辑的健壮调用
# ============================================================
# 【稳定性说明】
# 使用指数退避（exponential backoff）策略进行重试：
#   第1次重试等待 1 秒，第2次等待 2 秒，第3次等待 4 秒...
#   最大重试 MAX_RETRIES 次，确保 API 限流或网络波动时不会崩溃，
#   而是优雅地返回错误信息给用户。

MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # 指数退避基数(秒)

def call_ai_with_retry(ai_client, model_name, messages, extra_body=None):
    """
    带重试逻辑的 AI 调用封装。
    
    单向数据流说明：
    - 输入: messages 列表（只读，不会被修改）
    - 输出: (success: bool, content: str | error_msg: str)
    - 调用者负责将结果写入 session_state，保证数据流单向可追踪
    
    重试策略：
    - 网络错误（ConnectionError, Timeout）→ 重试
    - API 限流（429）→ 重试
    - 服务器错误（5xx）→ 重试
    - 客户端错误（4xx 非429）→ 不重试，立即返回错误
    """
    last_error = None
    extra = extra_body or {}
    
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = ai_client.chat.completions.create(
                model=model_name,
                messages=messages,
                stream=True,
                **extra
            )
            # 流式读取结果
            full_content = ""
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content is not None:
                    full_content += chunk.choices[0].delta.content
            
            if full_content:
                return True, full_content
            else:
                return False, "（模型返回了空内容，请重试）"
                
        except Exception as e:
            last_error = e
            error_str = str(e).lower()
            
            # 判断是否应该重试
            should_retry = (
                "connection" in error_str or
                "timeout" in error_str or
                "rate" in error_str or
                "429" in error_str or
                "503" in error_str or
                "502" in error_str or
                "500" in error_str or
                "overloaded" in error_str
            )
            
            if should_retry and attempt < MAX_RETRIES:
                wait_time = RETRY_BACKOFF_BASE ** (attempt + 1)
                print(f"[重试 {attempt + 1}/{MAX_RETRIES}] 等待 {wait_time}s 后重试... 错误: {e}")
                time.sleep(wait_time)
                continue
            else:
                break
    
    # 所有重试都失败，返回友好的错误信息
    error_msg = str(last_error) if last_error else "未知错误"
    if "Connection refused" in error_msg or "ConnectionError" in error_msg:
        if "Ollama" in st.session_state.model_provider:
            return False, (
                "❌ Ollama 未启动！请：\n"
                "1. 安装 Ollama: https://ollama.com\n"
                "2. 终端运行: `ollama serve`\n"
                "3. 下载模型: `ollama pull <模型名>`"
            )
        return False, f"❌ 无法连接 {st.session_state.model_provider}，请检查网络。"
    
    if "api key" in error_msg.lower() or "authentication" in error_msg.lower() or "401" in error_msg:
        return False, f"❌ API Key 无效或已过期，请检查 {st.session_state.model_provider} 的环境变量设置。"
    
    if "rate" in error_msg.lower() or "429" in error_msg:
        return False, "❌ API 调用频率超限，请稍后再试。"
    
    return False, f"❌ AI 调用失败（已重试 {MAX_RETRIES} 次）: {error_msg[:300]}"

def extract_code_blocks(text):
    """从文本中提取所有代码块，返回 [(语言, 代码), ...]"""
    pattern = r"```(\w*)\n(.*?)```"
    return re.findall(pattern, text, re.DOTALL)

def make_download_link(code, filename, label="📥 下载代码"):
    """生成代码下载链接"""
    b64 = base64.b64encode(code.encode()).decode()
    return f'<a href="data:text/plain;base64,{b64}" download="{filename}" style="text-decoration:none;">{label}</a>'

def create_file_from_code(code, filename):
    """在本地创建代码文件"""
    output_dir = Path(__file__).parent / "generated"
    output_dir.mkdir(exist_ok=True)
    filepath = output_dir / filename
    filepath.write_text(code, encoding="utf-8")
    return str(filepath)

# ============================================================
# 侧边栏
# ============================================================
with st.sidebar:
    logo_path = Path(__file__).parent.parent / '第六章' / 'image' / '29697107_111919603481_2.jpg'
    if logo_path.exists():
        st.logo(str(logo_path))

    # ========== 0. 模式切换 ==========
    st.subheader("🎯 工作模式")
    current_mode = current.get("mode", "伴侣")
    mode_options = ["💕 伴侣模式", "💻 编程助手模式"]
    cur_mode_idx = 0 if "伴侣" in current_mode else 1

    selected_mode = st.selectbox(
        "模式", mode_options, index=cur_mode_idx, label_visibility="collapsed"
    )
    new_mode = "伴侣" if "伴侣" in selected_mode else "编程"
    if new_mode != current.get("mode"):
        current["mode"] = new_mode
        provider = MODEL_PROVIDERS[st.session_state.model_provider]
        if new_mode == "编程":
            st.session_state.model_name = provider["default_coder_model"]
        else:
            st.session_state.model_name = provider["default_model"]
        save_sessions(st.session_state.sessions)
        st.rerun()

    is_coder = (current.get("mode") == "编程")
    st.caption("💕 智能聊天伴侣" if not is_coder else "💻 写代码 & 项目构建")

    st.divider()

    # ========== 1. AI 模型选择 ==========
    st.subheader("🤖 AI 模型")
    provider_names = list(MODEL_PROVIDERS.keys())
    cur_provider = st.session_state.model_provider
    prov_idx = provider_names.index(cur_provider) if cur_provider in provider_names else 0

    selected_provider = st.selectbox(
        "模型来源", provider_names, index=prov_idx, label_visibility="collapsed"
    )
    if selected_provider != st.session_state.model_provider:
        st.session_state.model_provider = selected_provider
        provider = MODEL_PROVIDERS[selected_provider]
        if is_coder:
            st.session_state.model_name = provider["default_coder_model"]
        else:
            st.session_state.model_name = provider["default_model"]
        st.rerun()

    provider = MODEL_PROVIDERS[st.session_state.model_provider]
    mode_key = "编程" if is_coder else "伴侣"
    model_list = provider["models"].get(mode_key, provider["models"]["伴侣"])

    cur_model = st.session_state.model_name
    model_idx = model_list.index(cur_model) if cur_model in model_list else 0
    selected_model = st.selectbox(
        "模型名称", model_list, index=model_idx, label_visibility="collapsed"
    )
    if selected_model != st.session_state.model_name:
        st.session_state.model_name = selected_model
        st.rerun()

    st.caption(f"*{provider['desc']}*")

    st.divider()

    # ========== 2. 伴侣模板（仅伴侣模式） ==========
    if not is_coder:
        st.subheader("🎭 伴侣模板")
        all_templates = get_all_templates()
        template_names = list(all_templates.keys())
        cur_tpl = current.get("template", "🌸 小甜甜")
        if cur_tpl not in template_names:
            cur_tpl = "🌸 小甜甜"
        tpl_idx = template_names.index(cur_tpl)

        selected = st.selectbox(
            "选择伴侣", template_names, index=tpl_idx, label_visibility="collapsed"
        )
        if selected and selected != current.get("template"):
            _apply_template_change(current, selected, all_templates)
            st.rerun()

        current_tpl = all_templates.get(current["template"], all_templates["🌸 小甜甜"])
        st.caption(f"*{current_tpl['desc']}*")

        # 随机切换
        rand_col1, rand_col2 = st.columns([1, 1])
        with rand_col1:
            if st.button("🎲 随机切换", use_container_width=True, key="random_switch"):
                available = [k for k in template_names if k != "🌟 新建自定义"]
                if available:
                    picked = random.choice(available)
                    _apply_template_change(current, picked, all_templates)
                    st.rerun()
        with rand_col2:
            if st.button("🔄 刷新", use_container_width=True, key="refresh_tpl"):
                st.rerun()

        # 自定义模板的保存/删除按钮
        is_custom = current["template"] not in BUILTIN_TEMPLATES and current["template"] != "🌟 新建自定义"

        if current["template"] == "🌟 新建自定义" or is_custom:
            save_col1, save_col2 = st.columns(2)
            with save_col1:
                save_name = st.text_input(
                    "模板名称",
                    value=current["template"] if is_custom else "",
                    placeholder="给模板起个名字...",
                    key=f"save_tpl_name_{st.session_state.current_session_id}"
                )
            with save_col2:
                st.caption("")
                if st.button("💾 保存模板", use_container_width=True, key=f"save_tpl_{st.session_state.current_session_id}"):
                    if save_name.strip() and current.get("nature", "").strip():
                        custom_templates = load_custom_templates()
                        if is_custom and current["template"] != save_name and current["template"] in custom_templates:
                            del custom_templates[current["template"]]
                        custom_templates[save_name.strip()] = {
                            "nick_name": current.get("nick_name", "").strip(),
                            "nature": current["nature"].strip(),
                            "desc": f"自定义: {current.get('nick_name', '').strip() if current.get('nick_name') else save_name.strip()}",
                            "background": current.get("background", "").strip()
                        }
                        save_custom_templates(custom_templates)
                        current["template"] = save_name.strip()
                        save_sessions(st.session_state.sessions)
                        record_update("保存模板", f"模板名称: {save_name.strip()}")
                        auto_sync_if_enabled(f"保存模板: {save_name.strip()}")
                        st.success(f"模板 '{save_name}' 已保存！下次打开可直接使用")
                        st.rerun()
                    else:
                        st.error("请填写模板名称和性格描述")

            if is_custom:
                if st.button("🗑️ 删除此模板", use_container_width=True, key=f"del_tpl_{st.session_state.current_session_id}"):
                    custom_templates = load_custom_templates()
                    old_name = current["template"]
                    if current["template"] in custom_templates:
                        del custom_templates[current["template"]]
                        save_custom_templates(custom_templates)
                    current["template"] = "🌸 小甜甜"
                    fallback = BUILTIN_TEMPLATES["🌸 小甜甜"]
                    current["nick_name"] = fallback.get("nick_name", "小甜甜")
                    current["nature"] = fallback["nature"]
                    current["background"] = fallback.get("background", "")
                    save_sessions(st.session_state.sessions)
                    record_update("删除模板", f"模板名称: {old_name}")
                    auto_sync_if_enabled(f"删除模板: {old_name}")
                    st.warning("模板已删除")
                    st.rerun()

        st.divider()

    # ========== 3. 历史会话管理 ==========
    st.subheader("💬 历史会话")

    if st.button("➕ 新建会话", use_container_width=True):
        new_id = str(uuid.uuid4())[:8]
        dt = "🌸 小甜甜"
        tpl = BUILTIN_TEMPLATES[dt]
        st.session_state.sessions[new_id] = {
            "name": f"新会话 {len(st.session_state.sessions) + 1}",
            "template": dt,
            "nick_name": tpl.get("nick_name", "小甜甜"),
            "nature": tpl["nature"],
            "background": tpl.get("background", ""),
            "mode": "伴侣",
            "messages": [],
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")
        }
        st.session_state.current_session_id = new_id
        save_sessions(st.session_state.sessions)
        auto_sync_if_enabled(f"新建会话: {st.session_state.sessions[new_id]['name']}")
        st.rerun()

    for sid in list(st.session_state.sessions.keys()):
        sess = st.session_state.sessions[sid]
        active = (sid == st.session_state.current_session_id)
        c1, c2, c3 = st.columns([5, 1, 1])

        mode_icon = "💻" if sess.get("mode") == "编程" else "💕"
        label = f"{'🟢 ' if active else ''}{mode_icon} {sess['name']}"
        if len(label) > 18:
            label = label[:15] + "..."

        with c1:
            if st.button(label, key=f"sw_{sid}", use_container_width=True,
                         type="primary" if active else "secondary",
                         help=f"创建于: {sess['created_at']}"):
                st.session_state.current_session_id = sid
                st.rerun()

        with c2:
            if st.button("✏️", key=f"rn_{sid}", help="重命名"):
                st.session_state[f"renaming_{sid}"] = True

        with c3:
            if len(st.session_state.sessions) > 1:
                if st.button("🗑️", key=f"del_{sid}", help="删除"):
                    del st.session_state.sessions[sid]
                    if st.session_state.current_session_id == sid:
                        st.session_state.current_session_id = list(st.session_state.sessions.keys())[0]
                    save_sessions(st.session_state.sessions)
                    auto_sync_if_enabled(f"删除会话: {sess['name']}")
                    st.rerun()

        if st.session_state.get(f"renaming_{sid}"):
            rn = st.text_input("新名称", value=sess["name"], key=f"rn_input_{sid}", label_visibility="collapsed")
            rc1, rc2 = st.columns(2)
            with rc1:
                if st.button("✅", key=f"rn_ok_{sid}"):
                    sess["name"] = rn if rn.strip() else sess["name"]
                    del st.session_state[f"renaming_{sid}"]
                    save_sessions(st.session_state.sessions)
                    auto_sync_if_enabled(f"重命名会话: {sess['name']}")
                    st.rerun()
            with rc2:
                if st.button("❌", key=f"rn_cancel_{sid}"):
                    del st.session_state[f"renaming_{sid}"]
                    st.rerun()

    st.divider()
    if st.button("🗑️ 清空所有会话", use_container_width=True):
        first_id = str(uuid.uuid4())[:8]
        dt = "🌸 小甜甜"
        tpl = BUILTIN_TEMPLATES[dt]
        st.session_state.sessions = {
            first_id: {
                "name": "默认会话", "template": dt,
                "nick_name": tpl.get("nick_name", "小甜甜"),
                "nature": tpl["nature"],
                "background": tpl.get("background", ""),
                "mode": "伴侣",
                "messages": [], "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")
            }
        }
        st.session_state.current_session_id = first_id
        save_sessions(st.session_state.sessions)
        st.rerun()

    # ========== 4. GitHub 同步 & 手机访问 ==========
    st.divider()
    st.subheader("🚀 GitHub 同步")

    # 自动同步开关
    if "auto_sync_enabled" not in st.session_state:
        st.session_state.auto_sync_enabled = False

    auto_sync = st.toggle(
        "🔄 实时自动推送",
        value=st.session_state.auto_sync_enabled,
        key="auto_sync_toggle",
        help="开启后，每次修改模板/发送消息后自动推送到GitHub"
    )
    if auto_sync != st.session_state.auto_sync_enabled:
        st.session_state.auto_sync_enabled = auto_sync
        if auto_sync:
            st.success("✅ 实时自动推送已开启，每次操作后将自动同步到GitHub")
        else:
            st.info("ℹ️ 实时自动推送已关闭，可手动推送")

    # 文件监控自动推送开关
    if "auto_watch_enabled" not in st.session_state:
        st.session_state.auto_watch_enabled = False

    auto_watch = st.toggle(
        "👁️ 监控代码文件变更自动推送",
        value=st.session_state.auto_watch_enabled,
        key="auto_watch_toggle",
        help="开启后，后台守护线程每30秒检测一次代码文件（.py/.js/.css等）变更，发现变更后自动 git add + commit + push 到GitHub"
    )
    if auto_watch != st.session_state.auto_watch_enabled:
        st.session_state.auto_watch_enabled = auto_watch
        if auto_watch:
            _start_auto_push_watcher()
            st.success("✅ 代码监控已启动，每30秒检测一次变更并自动推送")
        else:
            _stop_auto_push_watcher()
            st.info("ℹ️ 代码监控已关闭")

    # 自动拉取开关
    if "auto_pull_enabled" not in st.session_state:
        st.session_state.auto_pull_enabled = False

    auto_pull = st.toggle(
        "📥 启动时自动拉取更新",
        value=st.session_state.auto_pull_enabled,
        key="auto_pull_toggle",
        help="开启后，每次打开应用时自动从GitHub拉取最新代码"
    )
    if auto_pull != st.session_state.auto_pull_enabled:
        st.session_state.auto_pull_enabled = auto_pull
        if auto_pull:
            st.success("✅ 启动时自动拉取已开启，每次打开应用将自动更新")
        else:
            st.info("ℹ️ 启动时自动拉取已关闭，可手动拉取")

    git_col1, git_col2 = st.columns(2)
    with git_col1:
        if st.button("📤 手动推送", use_container_width=True, key="git_push"):
            commit_msg = f"手动更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            success, msg = git_auto_sync(commit_msg)
            if success:
                record_update("GitHub推送", msg)
                st.success(f"✅ {msg}")
            else:
                st.error(f"❌ {msg}")

    with git_col2:
        if st.button("📥 拉取更新", use_container_width=True, key="git_pull_btn"):
            with st.spinner("正在从 GitHub 拉取最新代码..."):
                success, msg = git_pull()
            if success:
                record_update("GitHub拉取", msg)
                if "已是最新" in msg:
                    st.info(f"ℹ️ {msg}")
                else:
                    st.success(f"✅ {msg}")
                    st.warning("⚠️ 代码已更新，请刷新页面以加载最新版本")
            else:
                st.error(f"❌ {msg}")

    git_col3, git_col4 = st.columns(2)
    with git_col3:
        if st.button("📋 更新日志", use_container_width=True, key="show_update_log"):
            st.session_state["show_update_log"] = True

    if st.session_state.get("show_update_log"):
        logs = load_update_log()
        if logs:
            st.caption("**最近更新记录:**")
            for log in reversed(logs[-10:]):
                st.caption(f"🕐 {log['time']} | {log['action']}: {log['detail']}")
            if st.button("关闭日志", key="close_log"):
                st.session_state["show_update_log"] = False
                st.rerun()
        else:
            st.caption("暂无更新记录")

    st.divider()
    st.subheader("📱 手机访问")
    with st.expander("如何让手机/外网访问？"):
        local_ip = get_local_ip()
        st.markdown(f"""
**方案一：ngrok 内网穿透（推荐，免费）**

1. 下载 ngrok: https://ngrok.com/download
2. 注册获取 authtoken（免费）
3. 终端运行：
   ```
   ngrok config add-authtoken <你的token>
   ngrok http 8501
   ```
4. 复制显示的 `https://xxx.ngrok-free.app` 地址
5. 把这个地址发到微信，手机随时打开

**方案二：Streamlit Cloud（免费，推荐手机使用）**

1. 代码已推送到 GitHub 后自动部署
2. 访问 https://streamlit.io/cloud
3. 连接 GitHub 仓库: `lyxznl050924/yyssyy`
4. 一键部署，获得永久公网地址
5. 手机浏览器打开即可使用

**方案三：局域网访问（需同WiFi）**

手机浏览器访问: `http://{local_ip}:8501`
        """)

    st.divider()

# ============================================================
# 主区域 —— 伴侣预览 + 聊天展示
# ============================================================
# 布局：左侧侧边栏(25%) = 配置中心，右侧(75%) = 只读预览 + 聊天
# 数据流：侧边栏编辑器 → session_state → 主区域只读预览
# ┌─────────────────────────────────────────────────┐
# │ 侧边栏(配置)          │ 主区域(展示)            │
# │  🎭 模板选择          │  🤖 伴侣预览卡片(只读)  │
# │  ✏️ 昵称/性格编辑     │  💬 聊天消息            │
# │  💬 会话管理          │  📥 chat_input          │
# │  🚀 GitHub同步        │                         │
# └─────────────────────────────────────────────────┘

is_coder = (current.get("mode") == "编程")

# ============================================================
# 顶部：伴侣信息卡片（可编辑 + 自动保存 + 防抖）
# ============================================================
# 数据流（React useState / Vue Pinia 等价模式）：
#   st.session_state.sessions[id] 是 Single Source of Truth
#   ┌─────────────────────────────────────────────────┐
#   │ 侧边栏下拉切换模板 → 写入 session_state        │
#   │     ↓ st.rerun() 自动重渲染                    │
#   │ 右侧卡片从 session_state 读取最新值（预填充）  │
#   │     ↓ 用户编辑输入框 / textarea                │
#   │ 失焦/Enter → 写入 session_state → save_sessions│
#   │     ↓ 自动重渲染 → 侧边栏模板列表实时同步      │
#   └─────────────────────────────────────────────────┘
#
# 【防抖说明】
# Streamlit 原生 text_input/text_area 仅在失焦或 Ctrl+Enter 时触发
# rerun，天然等效于 500-800ms 防抖。用户打字期间不会触发保存，
# 停止输入并点击外部区域 → 自动保存 + "✅ 已自动保存" Toast。

if not is_coder:
    with st.container(border=True):
        st.markdown("""
        <style>
        section[data-testid="stSidebar"] {
            border-right: 1px solid rgba(255, 255, 255, 0.08);
        }
        </style>
        """, unsafe_allow_html=True)

        tpl = get_all_templates().get(current["template"], BUILTIN_TEMPLATES["🌸 小甜甜"])

        # --- 头像 + 模板名称 ---
        avatar_map = {
            "🌸": "🌸", "💎": "💎", "🍬": "🍬", "☕": "☕",
            "🎮": "🎮", "📚": "📚", "🎤": "🎤", "🌙": "🌙",
            "🐱": "🐱", "🦊": "🦊", "🐰": "🐰", "🐶": "🐶",
            "🌟": "✨", "💻": "💻"
        }
        avatar_emoji = "🤖"
        for key, emoji in avatar_map.items():
            if current["template"].startswith(key):
                avatar_emoji = emoji
                break

        col_avatar, col_title = st.columns([1, 5])
        with col_avatar:
            st.markdown(f"""
            <div style="width:60px;height:60px;border-radius:50%;
            background:linear-gradient(135deg,#667eea,#764ba2);
            display:flex;align-items:center;justify-content:center;
            font-size:30px;margin-top:10px;">{avatar_emoji}</div>
            """, unsafe_allow_html=True)
        with col_title:
            st.markdown(f"### {current['template']}")
            st.caption(tpl["desc"])

        st.divider()

        # ================================================================
        # 【handleInputChange】—— 检测字段变化 → 自动写入 state
        # ================================================================
        st.caption("💬 **昵称**")
        card_nick = st.text_input(
            "昵称",
            value=current.get("nick_name", ""),
            key=f"card_nick_{st.session_state.current_session_id}_{current['template']}",
            label_visibility="collapsed",
            placeholder="给伴侣起个昵称..."
        )

        st.caption("📝 **性格描述**")
        card_nature = st.text_area(
            "性格描述",
            value=current.get("nature", ""),
            height=100,
            key=f"card_nature_{st.session_state.current_session_id}_{current['template']}",
            label_visibility="collapsed",
            placeholder="描述伴侣的性格特点...",
            help="修改后点击外部区域或按 Ctrl+Enter 自动保存"
        )

        st.caption("🌅 **背景故事**")
        card_background = st.text_area(
            "背景故事",
            value=current.get("background", ""),
            height=80,
            key=f"card_background_{st.session_state.current_session_id}_{current['template']}",
            label_visibility="collapsed",
            placeholder="伴侣的过往经历、背景设定...",
            help="选填，丰富角色设定"
        )

        # ================================================================
        # 【debounceSave】—— 失焦自动保存（等效 500ms 防抖）
        # ================================================================
        changed = False
        if card_nick != current.get("nick_name", ""):
            current["nick_name"] = card_nick.strip()
            changed = True
        if card_nature != current.get("nature", ""):
            current["nature"] = card_nature.strip()
            changed = True
        if card_background != current.get("background", ""):
            current["background"] = card_background.strip()
            changed = True

        if changed:
            save_sessions(st.session_state.sessions)
            st.toast(f"✅ 已自动保存 ({datetime.now().strftime('%H:%M:%S')})", icon="✅")

        st.divider()

        # ============================================================
        # 模板保存/删除（带重复名称检查）
        # ============================================================
        is_custom = current["template"] not in BUILTIN_TEMPLATES and current["template"] != "🌟 新建自定义"
        is_new = current["template"] == "🌟 新建自定义"

        if is_custom or is_new:
            st.caption("💾 **保存为模板**")
            save_col1, save_col2 = st.columns([3, 1])
            with save_col1:
                save_name = st.text_input(
                    "模板名称",
                    value=current["template"] if is_custom else "",
                    placeholder="起个名字保存...",
                    key=f"card_save_name_{st.session_state.current_session_id}_{current['template']}",
                    label_visibility="collapsed"
                )
            with save_col2:
                if st.button("💾", use_container_width=True, key=f"card_save_btn_{st.session_state.current_session_id}_{current['template']}",
                             help="保存当前设置为新模板"):
                    name = save_name.strip()
                    if not name:
                        st.error("请输入模板名称")
                    elif not current.get("nature", "").strip():
                        st.error("请先填写性格描述")
                    else:
                        custom_templates = load_custom_templates()
                        # 重复名称检查（防错处理）
                        if name in custom_templates and name != current["template"]:
                            st.error(f"模板 '{name}' 已存在，请换一个名称")
                        elif name in BUILTIN_TEMPLATES:
                            st.error(f"'{name}' 是内置模板，不能覆盖")
                        else:
                            if is_custom and current["template"] != name and current["template"] in custom_templates:
                                del custom_templates[current["template"]]
                            custom_templates[name] = {
                                "nick_name": current.get("nick_name", "").strip(),
                                "nature": current["nature"].strip(),
                                "desc": f"自定义: {current.get('nick_name', name).strip()}",
                                "background": current.get("background", "").strip()
                            }
                            save_custom_templates(custom_templates)
                            current["template"] = name
                            save_sessions(st.session_state.sessions)
                            record_update("保存模板", f"模板名称: {name}")
                            auto_sync_if_enabled(f"保存模板: {name}")
                            st.success(f"✅ 模板 '{name}' 已保存！")
                            st.rerun()

            if is_custom:
                if st.button("🗑️ 删除此模板", use_container_width=True, key=f"card_del_btn_{st.session_state.current_session_id}_{current['template']}"):
                    custom_templates = load_custom_templates()
                    old_name = current["template"]
                    if old_name in custom_templates:
                        del custom_templates[old_name]
                        save_custom_templates(custom_templates)
                    current["template"] = "🌸 小甜甜"
                    fallback = BUILTIN_TEMPLATES["🌸 小甜甜"]
                    current["nick_name"] = fallback.get("nick_name", "小甜甜")
                    current["nature"] = fallback["nature"]
                    current["background"] = fallback.get("background", "")
                    save_sessions(st.session_state.sessions)
                    record_update("删除模板", f"模板名称: {old_name}")
                    auto_sync_if_enabled(f"删除模板: {old_name}")
                    st.warning("模板已删除")
                    st.rerun()

        # --- 底部统计 ---
        st.caption(f"📋 **消息数:** {len(current.get('messages', []))} 条  |  🕐 {current.get('created_at', '')}")

    st.divider()
else:
    st.title("💻 编程助手")
    st.caption("写代码 · 项目构建 · 代码审查 · 技术问答")

# ============================================================
# 聊天区域
# ============================================================
if not current["messages"]:
    if is_coder:
        tips = [
            "💡 试试说：'用 Python 写一个 FastAPI 后端'",
            "💡 试试说：'帮我搭建一个 React 项目'",
            "💡 试试说：'这段代码有什么问题？' + 粘贴代码",
            "💡 试试说：'写一个 SQL 查询，统计每月销量'",
            "💡 试试说：'帮我优化这段代码的性能'",
            "💡 试试说：'生成一个完整的 Dockerfile'",
        ]
    else:
        tips = [
            "💡 试试说：'今天过得好吗？'",
            "💡 试试说：'讲个笑话给我听'",
            "💡 试试说：'我心情不太好...'",
            "💡 试试说：'你叫什么名字呀？'",
            "💡 试试说：'晚安~'",
            "💡 试试说：'给我讲个故事'",
        ]
    st.info(random.choice(tips))

for i, message in enumerate(current["messages"]):
    with st.chat_message(message["role"]):
        content = message["content"]
        st.write(content)

        # 如果是编程模式且回复包含代码块，显示下载按钮
        if is_coder and message["role"] == "assistant":
            code_blocks = extract_code_blocks(content)
            if code_blocks:
                for lang, code in code_blocks:
                    ext_map = {
                        "python": "py", "javascript": "js", "typescript": "ts",
                        "html": "html", "css": "css", "java": "java",
                        "cpp": "cpp", "c": "c", "go": "go", "rust": "rs",
                        "sql": "sql", "shell": "sh", "bash": "sh",
                        "yaml": "yml", "json": "json", "dockerfile": "Dockerfile",
                    }
                    ext = ext_map.get(lang.lower(), "txt")
                    filename = f"generated_{i}_{random.randint(100,999)}.{ext}"
                    dl_link = make_download_link(code, filename, f"📥 下载 {lang.upper() if lang else '代码'}")
                    st.markdown(dl_link, unsafe_allow_html=True)

                    # 创建文件到本地
                    if st.button(f"💾 保存到本地: {filename}", key=f"save_{i}_{random.randint(1000,9999)}"):
                        saved_path = create_file_from_code(code, filename)
                        st.success(f"已保存到: {saved_path}")

# ============================================================
# 底部
# ============================================================
local_ip = get_local_ip()
st.caption(f"💡 局域网: http://{local_ip}:8501 | 公网访问请用 ngrok（见侧边栏指南）")

# ============================================================
# 输入框与AI交互
# ============================================================
placeholder_text = "请输入编程问题..." if is_coder else "请输入您的问题..."
prompt = st.chat_input(placeholder_text)

if prompt:
    st.chat_message("user").write(prompt)
    print(f"----------> 调用AI, 用户: {prompt}")
    current["messages"].append({"role": "user", "content": prompt})
    save_sessions(st.session_state.sessions)

    ai_client, error = get_client()
    if error:
        st.error(error)
    else:
        model_name = st.session_state.model_name

        if is_coder:
            system_content = CODER_SYSTEM_PROMPT
        else:
            system_content = PARTNER_SYSTEM_PROMPT % (
                current.get("nick_name", "小甜甜"),
                current.get("nature", "活泼开朗")
            )

        # 使用带重试逻辑的 API 封装层
        success, result = call_ai_with_retry(
            ai_client=ai_client,
            model_name=model_name,
            messages=[
                {"role": "system", "content": system_content},
                *current["messages"]
            ],
            extra_body=MODEL_PROVIDERS[st.session_state.model_provider].get("extra_body", {})
        )

        if success:
            full_reply = result
            st.chat_message("assistant").write(full_reply)

            print(f"<---------- AI回复: {full_reply[:50]}...")
            current["messages"].append({"role": "assistant", "content": full_reply})
            save_sessions(st.session_state.sessions)
            record_update("AI对话", f"伴侣: {current.get('nick_name', '')}, 消息数: {len(current['messages'])}")
            auto_sync_if_enabled(f"AI对话 - {current.get('nick_name', '小甜甜')}")

            if is_coder:
                code_blocks = extract_code_blocks(full_reply)
                if code_blocks:
                    for lang, code in code_blocks:
                        ext_map = {
                            "python": "py", "javascript": "js", "typescript": "ts",
                            "html": "html", "css": "css", "java": "java",
                            "cpp": "cpp", "go": "go", "rust": "rs",
                            "sql": "sql", "shell": "sh", "yaml": "yml",
                            "json": "json", "dockerfile": "Dockerfile",
                        }
                        ext = ext_map.get(lang.lower(), "txt")
                        filename = f"generated_{random.randint(1000,9999)}.{ext}"
                        dl_link = make_download_link(code, filename, f"📥 下载 {lang.upper() if lang else '代码'}")
                        st.markdown(dl_link, unsafe_allow_html=True)
                        if st.button(f"💾 保存到本地: {filename}", key=f"save_new_{random.randint(10000,99999)}"):
                            saved_path = create_file_from_code(code, filename)
                            st.success(f"已保存到: {saved_path}")

            st.rerun()
        else:
            st.error(result)
            print(f"<---------- 错误: {result}")