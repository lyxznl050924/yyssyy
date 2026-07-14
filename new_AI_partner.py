import streamlit as st
import os
import json
import re
import subprocess
import tempfile
import base64
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
# 伴侣模板
# ============================================================
CUSTOM_TEMPLATES_FILE = Path(__file__).parent / "custom_templates.json"

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

BUILTIN_TEMPLATES = {
    "🌸 小甜甜": {
        "nature": "活泼开朗的东北姑娘，热情直爽，喜欢用'咱'、'老妹儿'等东北话，说话带感叹号",
        "desc": "阳光开朗的东北甜心"
    },
    "💎 冷艳御姐": {
        "nature": "高冷傲娇的御姐，话少但字字珠玑，偶尔流露出温柔，用'哼'、'哦'等语气词",
        "desc": "冰山美人，外冷内热"
    },
    "🍬 温柔学妹": {
        "nature": "温柔体贴的学妹，说话轻声细语，喜欢用'学长/学姐'称呼，带波浪号~",
        "desc": "软萌治愈系学妹"
    },
    "☕ 知心姐姐": {
        "nature": "成熟稳重的知心姐姐，温柔但有边界，善于倾听和给建议，说话像散文一样优美",
        "desc": "温暖治愈的心灵港湾"
    },
    "🔥 毒舌女友": {
        "nature": "刀子嘴豆腐心的女友，嘴上不饶人但心里很在乎，喜欢吐槽但行动上很关心",
        "desc": "嘴毒心软的傲娇女友"
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
# 模型列表
# ============================================================
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
    "☁️ DeepSeek（云端）": {
        "base_url": "https://api.deepseek.com",
        "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
        "models": {
            "伴侣": ["deepseek-r1", "deepseek-chat", "deepseek-reasoner"],
            "编程": ["deepseek-r1", "deepseek-chat", "deepseek-reasoner"],
        },
        "default_model": "deepseek-r1",
        "default_coder_model": "deepseek-r1",
        "desc": "默认使用DeepSeek-R1推理模型，需API Key",
        "extra_body": {},
    },
}

# ============================================================
# 初始化 Session State
# ============================================================
if "sessions" not in st.session_state:
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
    st.session_state.current_session_id = first_id

if "current_session_id" not in st.session_state:
    st.session_state.current_session_id = list(st.session_state.sessions.keys())[0]

if "model_provider" not in st.session_state:
    st.session_state.model_provider = "☁️ DeepSeek（云端）"

if "model_name" not in st.session_state:
    st.session_state.model_name = "deepseek-r1"

current = st.session_state.sessions[st.session_state.current_session_id]

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
    provider = MODEL_PROVIDERS[st.session_state.model_provider]
    api_key = provider["api_key"]
    if not api_key and "DeepSeek" in st.session_state.model_provider:
        return None, "请先设置 DEEPSEEK_API_KEY 环境变量"
    return OpenAI(api_key=api_key, base_url=provider["base_url"]), None

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
        # 切换模式时自动切换模型
        provider = MODEL_PROVIDERS[st.session_state.model_provider]
        if new_mode == "编程":
            st.session_state.model_name = provider["default_coder_model"]
        else:
            st.session_state.model_name = provider["default_model"]
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
            if selected == "🌟 新建自定义":
                # 切换到自定义模板但不改变当前模板名
                current["template"] = "🌟 新建自定义"
                current["nick_name"] = ""
                current["nature"] = ""
                st.rerun()
            else:
                tpl = all_templates[selected]
                current["template"] = selected
                current["nick_name"] = selected.split(" ", 1)[1] if " " in selected else selected
                current["nature"] = tpl["nature"]
                st.rerun()

        current_tpl = all_templates.get(current["template"], all_templates["🌸 小甜甜"])
        st.caption(f"*{current_tpl['desc']}*")

        st.subheader("✏️ 伴侣信息")
        new_nick = st.text_input(
            "昵称", value=current.get("nick_name", "小甜甜"),
            key=f"nick_{st.session_state.current_session_id}"
        )
        if new_nick:
            current["nick_name"] = new_nick

        new_nature = st.text_area(
            "性格描述", value=current.get("nature", ""), height=60,
            key=f"nature_{st.session_state.current_session_id}"
        )
        if new_nature:
            current["nature"] = new_nature

        # 自定义模板的保存/删除按钮
        is_custom = current["template"] not in BUILTIN_TEMPLATES and current["template"] != "🌟 新建自定义"
        is_builtin = current["template"] in BUILTIN_TEMPLATES

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
                st.caption("")  # 占位对齐
                if st.button("💾 保存模板", use_container_width=True, key=f"save_tpl_{st.session_state.current_session_id}"):
                    if save_name.strip() and new_nature.strip():
                        custom_templates = load_custom_templates()
                        # 如果重命名了，删除旧模板
                        if is_custom and current["template"] != save_name and current["template"] in custom_templates:
                            del custom_templates[current["template"]]
                        custom_templates[save_name.strip()] = {
                            "nature": new_nature.strip(),
                            "desc": f"自定义: {new_nick.strip() if new_nick else save_name.strip()}"
                        }
                        save_custom_templates(custom_templates)
                        current["template"] = save_name.strip()
                        st.success(f"模板 '{save_name}' 已保存！下次打开可直接使用")
                        st.rerun()
                    else:
                        st.error("请填写模板名称和性格描述")

            if is_custom:
                if st.button("🗑️ 删除此模板", use_container_width=True, key=f"del_tpl_{st.session_state.current_session_id}"):
                    custom_templates = load_custom_templates()
                    if current["template"] in custom_templates:
                        del custom_templates[current["template"]]
                        save_custom_templates(custom_templates)
                    current["template"] = "🌸 小甜甜"
                    current["nick_name"] = "小甜甜"
                    current["nature"] = BUILTIN_TEMPLATES["🌸 小甜甜"]["nature"]
                    st.warning("模板已删除")
                    st.rerun()

        st.divider()

    # ========== 3. 历史会话管理 ==========
    st.subheader("💬 历史会话")

    if st.button("➕ 新建会话", use_container_width=True):
        new_id = str(uuid.uuid4())[:8]
        dt = "🌸 小甜甜"
        st.session_state.sessions[new_id] = {
            "name": f"新会话 {len(st.session_state.sessions) + 1}",
            "template": dt,
            "nick_name": "小甜甜",
            "nature": BUILTIN_TEMPLATES[dt]["nature"],
            "mode": "伴侣",
            "messages": [],
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")
        }
        st.session_state.current_session_id = new_id
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
                    st.rerun()

        if st.session_state.get(f"renaming_{sid}"):
            rn = st.text_input("新名称", value=sess["name"], key=f"rn_input_{sid}", label_visibility="collapsed")
            rc1, rc2 = st.columns(2)
            with rc1:
                if st.button("✅", key=f"rn_ok_{sid}"):
                    sess["name"] = rn if rn.strip() else sess["name"]
                    del st.session_state[f"renaming_{sid}"]
                    st.rerun()
            with rc2:
                if st.button("❌", key=f"rn_cancel_{sid}"):
                    del st.session_state[f"renaming_{sid}"]
                    st.rerun()

    st.divider()
    if st.button("🗑️ 清空所有会话", use_container_width=True):
        first_id = str(uuid.uuid4())[:8]
        dt = "🌸 小甜甜"
        st.session_state.sessions = {
            first_id: {
                "name": "默认会话", "template": dt, "nick_name": "小甜甜",
                "nature": BUILTIN_TEMPLATES[dt]["nature"], "mode": "伴侣",
                "messages": [], "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")
            }
        }
        st.session_state.current_session_id = first_id
        st.rerun()

    # ========== 4. 公网部署指南 ==========
    st.divider()
    st.subheader("🌐 公网访问")
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

**方案二：Streamlit Cloud（免费）**

1. 把代码上传到 GitHub
2. 访问 https://streamlit.io/cloud
3. 连接 GitHub 仓库，一键部署
4. 获得永久公网地址

**方案三：局域网访问（需同WiFi）**

手机浏览器访问: `http://{local_ip}:8501`
        """)

# ============================================================
# 主区域
# ============================================================
is_coder = (current.get("mode") == "编程")

col_left, col_main, col_right = st.columns([1, 3, 1])

with col_main:
    if is_coder:
        st.title("💻 编程助手")
        st.caption("写代码 · 项目构建 · 代码审查 · 技术问答")
    else:
        st.title(current["template"])
        st.caption(
            f"📋 {current['name']} | 💬 {len(current['messages'])} 条 | 🤖 {st.session_state.model_name}"
        )

with col_right:
    with st.container(border=True):
        if is_coder:
            st.markdown("### 💻 编程助手")
            st.caption("全栈开发 · 项目构建")
            st.markdown(f"**模型:** {st.session_state.model_name}")
        else:
            tpl = get_all_templates().get(current["template"], BUILTIN_TEMPLATES["🌸 小甜甜"])
            st.markdown(f"### {current['template']}")
            st.caption(tpl["desc"])
            st.markdown(f"**性格:** {current['nature']}")

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

    ai_client, error = get_client()
    if error:
        st.error(error)
    else:
        model_name = st.session_state.model_name

        # 根据模式选择系统提示词
        if is_coder:
            system_content = CODER_SYSTEM_PROMPT
        else:
            system_content = PARTNER_SYSTEM_PROMPT % (
                current.get("nick_name", "小甜甜"),
                current.get("nature", "活泼开朗")
            )

        try:
            response = ai_client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_content},
                    *current["messages"]
                ],
                stream=True,
                **MODEL_PROVIDERS[st.session_state.model_provider].get("extra_body", {})
            )

            placeholder = st.empty()
            full_reply = ""
            for chunk in response:
                if chunk.choices[0].delta.content is not None:
                    content = chunk.choices[0].delta.content
                    full_reply += content
                    placeholder.chat_message("assistant").write(full_reply)

            if not full_reply:
                full_reply = "（未收到回复，请检查模型是否正常运行）"
                st.chat_message("assistant").write(full_reply)

            print(f"<---------- AI回复: {full_reply[:50]}...")
            current["messages"].append({"role": "assistant", "content": full_reply})

            # 编程模式：自动检测代码块并显示下载按钮
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

        except Exception as e:
            if "Connection refused" in str(e) or "ConnectionError" in str(e):
                msg = f"❌ 无法连接 {st.session_state.model_provider}。\n\n"
                if "Ollama" in st.session_state.model_provider:
                    msg += (
                        "**Ollama 未启动！** 请按以下步骤操作：\n"
                        "1. 下载安装 Ollama: https://ollama.com\n"
                        "2. 打开终端运行: `ollama serve`\n"
                        "3. 下载模型: `ollama pull qwen2.5-coder:7b`\n"
                        "4. 刷新本页面重试"
                    )
                else:
                    msg += "请检查网络连接和 API Key 是否有效。"
                st.error(msg)
            else:
                st.error(f"AI 调用失败: {e}")
            print(f"<---------- 错误: {e}")