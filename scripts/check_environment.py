"""Nectivon v0.8.7 Environment and Provider Configuration Diagnostic Tool."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def check_python_environment() -> bool:
    print("=" * 65)
    print(" 1. Python 运行环境检查")
    print("=" * 65)
    version_info = sys.version_info
    ver_str = f"{version_info.major}.{version_info.minor}.{version_info.micro}"
    passed = version_info >= (3, 11)

    print(f"  - 当前 Python 版本 : {ver_str}")
    print(f"  - 解释器路径      : {sys.executable}")
    if passed:
        print("  - 版本合规状态    : [通过] 符合 Python >= 3.11 要求")
    else:
        print("  - 版本合规状态    : [未通过] Nectivon 需要 Python 3.11 或更高版本")
    print()
    return passed


def check_virtual_environment() -> bool:
    print("=" * 65)
    print(" 2. 虚拟环境检查 (.venv)")
    print("=" * 65)
    venv_dir = PROJECT_ROOT / ".venv"
    venv_py = venv_dir / "Scripts" / "python.exe"

    if venv_py.is_file():
        print(f"  - .venv 虚拟环境  : [已就绪] {venv_dir}")
        print(f"  - venv 解释器     : {venv_py}")
        return True
    else:
        print(f"  - .venv 虚拟环境  : [未找到] 路径: {venv_dir}")
        print("    (提示: 首次使用可运行 启动正式版.bat 或使用 'uv sync' 创建)")
        return False


def check_runtime_dependencies() -> bool:
    print("=" * 65)
    print(" 3. 运行依赖检查")
    print("=" * 65)
    modules = {
        "streamlit": "streamlit",
        "PyMuPDF": "fitz",
        "Pillow": "PIL",
        "python-dotenv": "dotenv",
        "pydantic-settings": "pydantic_settings",
        "FastAPI": "fastapi",
        "Uvicorn": "uvicorn",
        "jieba": "jieba",
        "rapidfuzz": "rapidfuzz",
    }
    missing = [name for name, module in modules.items() if importlib.util.find_spec(module) is None]
    if missing:
        print(f"  - 依赖状态        : [未通过] 缺少：{', '.join(missing)}")
        print("  - 安装命令        : python -m pip install -r requirements\\requirements.txt")
        print()
        return False
    print(f"  - 依赖状态        : [通过] 已检查 {len(modules)} 个运行依赖")
    print()
    return True


def check_api_config_files() -> None:
    print("=" * 65)
    print(" 4. 配置文件检查 (.env / ai-providers-v1.json)")
    print("=" * 65)
    env_file = PROJECT_ROOT / ".env"
    example_file = PROJECT_ROOT / ".env.example"

    if env_file.is_file():
        print(f"  - 本地 .env 文件   : [已存在] {env_file}")
    else:
        print("  - 本地 .env 文件   : [未创建] (完全离线模式运行正常)")
        if example_file.is_file():
            print(f"    (参考模板: {example_file})")

    try:
        from src.ai.credential_store import default_dpapi_credential_path
        from src.ai.provider_config import default_provider_config_path

        cfg_path = default_provider_config_path()
        if cfg_path.is_file():
            print(f"  - Provider 配置文件: [已存在] {cfg_path}")
        else:
            print(f"  - Provider 配置文件: [未生成] {cfg_path}")
            print("    (在 Web 界面【系统设置 → AI / 模型服务】中保存后自动生成)")
        print(f"  - 加密凭据文件    : {default_dpapi_credential_path()}")
        print("    (API Key 不写入 Provider JSON；正式环境优先使用 Windows 凭据管理器)")
    except Exception as exc:
        print(f"  - Provider 配置文件读取提示: {exc}")
    print()


def check_five_providers() -> None:
    print("=" * 65)
    print(" 5. 五个 AI Provider 配置状态检查 (Qwen/DeepSeek/Kimi/Hunyuan/GLM)")
    print("=" * 65)
    from src.ai.model_registry import ProviderId, get_provider_definition

    providers_info = [
        (ProviderId.QWEN, "Qwen / 阿里通义千问"),
        (ProviderId.DEEPSEEK, "DeepSeek / 深度求索"),
        (ProviderId.KIMI, "Kimi / 月之暗面"),
        (ProviderId.HUNYUAN, "Hunyuan / 腾讯混元"),
        (ProviderId.GLM, "GLM / 智谱清言"),
    ]

    try:
        from src.runtime import application_provider_settings_service
        service = application_provider_settings_service()
        state, active_provider, active_model = service.current_state()

        print(f"  - 全局 AI 状态    : {state.value}")
        print(f"  - 当前主 Provider : {active_provider.value if active_provider else '无'}")
        print(f"  - 当前主模型      : {active_model or '无'}")
        print("-" * 65)

        for pid, name in providers_info:
            definition = get_provider_definition(pid)
            try:
                view = service.view(pid)
                has_credential = view.credential_saved
                is_active = view.active
                configured_model = (
                    view.settings.model_id if view.settings else definition.default_model_id
                )
            except Exception:
                has_credential = False
                is_active = False
                configured_model = definition.default_model_id

            status_tag = "[已配置凭据]" if has_credential else "[未配置凭据]"
            active_tag = " <--- 当前激活" if is_active else ""
            line_out = f"  [{pid.value:<8}] {name:<22} : {status_tag} 模型: {configured_model}"
            print(f"{line_out}{active_tag}")
    except Exception as err:
        print(f"  - Provider 服务检查提示: {err}")
        print("  - 支持在 Web 界面【系统设置 → AI / 模型服务】中安全配置各个 Provider。")
    print("=" * 65)
    print("提示: Nectivon 遵循 Local First 原则，未配置 AI 凭据时，全部核心本地知识")
    print("      管理、文档检索与针对训练功能均 100% 离线可用。")
    print("=" * 65)


def main() -> int:
    py_ok = check_python_environment()
    check_virtual_environment()
    dependencies_ok = check_runtime_dependencies()
    check_api_config_files()
    check_five_providers()
    return 0 if py_ok and dependencies_ok else 1


if __name__ == "__main__":
    sys.exit(main())
