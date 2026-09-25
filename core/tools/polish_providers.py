"""
二次整理可选的在线 LLM 服务商 (客户端托盘菜单 + 服务端调用共用这一份).

加服务商: 在 PROVIDERS 里加一项. 要求 OpenAI 兼容的 /chat/completions 接口, 且能关掉"思考" (否则一句要等好几秒).
key 只从环境变量读; 某些服务商允许回退读它自家命令行工具的配置文件 (key_file, 只读, 不复制到别处).
实测延迟 (2026-09-23, 关思考): DeepSeek V4 Flash 0.4-1.2s; MiniMax M3 0.7-2.3s; MiMo V2.6 Flash 中位 0.74s 但 3/8 次 >3s (最慢 13s);
MiniMax M2.7 关不掉思考 3-6s, 不收录. 2026-09-26 评测 (DeepSeek V4 Flash 37/38 中位 0.85s): Kimi K3 36/38 1.55s (K2.8 32/38); GLM-5.3 Flash 35/38 1.25s;
DeepSeek V4 Pro 36/38 1.6s 且更贵, 不收录.
"""
import json
import os

PROVIDERS = {
    'deepseek': {
        'name': 'DeepSeek V4 Flash',
        'url': 'https://api.deepseek.com/chat/completions',
        'model': 'deepseek-v4-flash',
        'key_env': 'DEEPSEEK_API_KEY',
    },
    'minimax': {
        'name': 'MiniMax M3',
        'url': 'https://api.minimaxi.com/v1/chat/completions',
        'model': 'MiniMax-M3',
        'key_env': 'MINIMAX_API_KEY',
        'key_file': ('~/.mmx/config.json', 'api_key'),   # mmx-cli 登录后存的 key
    },
    'mimo': {
        'name': 'MiMo V2.6 Flash',
        'url': 'https://token-plan-cn.xiaomimimo.com/v1/chat/completions',
        'model': 'mimo-v2.6-flash',
        'key_env': 'MIMO_API_KEY',
    },
    'kimi': {                        # Kimi Code 会员订阅地址 (不是 api.moonshot.cn 按量计费); 关思考后 1.5s
        'name': 'Kimi K3',
        'url': 'https://api.kimi.com/coding/v1/chat/completions',
        'model': 'k3',                   # 2026-09-26 评测 K3 36/38 中位 1.55s, K2.8 (kimi-for-coding) 32/38
        'key_env': 'KIMI_API_KEY',
        'body': {'temperature': 0.6},    # 该模型只接受 0.6, 传 0 报 400
    },
    'zhipu': {                       # 智谱 GLM Coding Plan 地址: 这里能关思考; 按量计费地址 /api/paas/v4 报"始终思考"
        'name': 'GLM-5.3 Flash',
        'url': 'https://open.bigmodel.cn/api/coding/paas/v4/chat/completions',
        'model': 'glm-5.3-flash',
        'key_env': 'ZHIPU_API_KEY',
    },
}
# 2026-09-24 试过 Groq gpt-oss-120b, 不收录: 免费档按每分钟 token 限流, 评测 26 次里 18 次 429; 单次 0.9s (生成 0.05s).
# 真要加回, 配置是: url https://api.groq.com/openai/v1/chat/completions, model openai/gpt-oss-120b, key_env GROQ_API_KEY,
# body {'thinking': None, 'max_tokens': None, 'reasoning_effort': 'low', 'include_reasoning': False}
# (推理模型关不掉思考只能 low; 思考 token 计入上限; 不认 thinking 字段). 服务商字段 body 覆盖请求参数, None = 删掉.
# 同日试过本机 Ollama (WSL 容器, RTX 4060), 不收录: qwen3.5:4b 热 0.65s 但评测 16/26 (DeepSeek 26/26), 冷加载 7.7s 超时限,
# 与识别模型抢 8GB 显存; lfm2.5:8b 关不掉思考 (每次 2376 token, 15s). 配置: url http://localhost:11434/v1/chat/completions,
# body {'thinking': None, 'reasoning_effort': 'none'}, key 随便填.
DEFAULT = 'deepseek'


def resolve(value) -> str:
    """客户端传来的 polish 字段 -> 服务商 id; '' = 不整理. 兼容旧版的 True/False."""
    if value is True:
        return DEFAULT
    if isinstance(value, str) and value in PROVIDERS:
        return value
    return ''


def _user_env(name: str) -> str:
    """进程启动后才设的用户环境变量, os.environ 里没有 (要等重新登录); 直接读注册表 HKCU/Environment"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as k:
            return str(winreg.QueryValueEx(k, name)[0] or '')
    except OSError:
        return ''


def env_key(name: str) -> str:
    """注册表优先: 换 key 后不用重启就用新值; 进程继承的 os.environ 可能是旧 key
    (部署脚本从已开着的终端拉起 CapsWriter, 继承的是终端启动时的环境)"""
    return _user_env(name) or os.environ.get(name, '')


def api_key(pid: str) -> str:
    p = PROVIDERS[pid]
    key = env_key(p['key_env'])
    if not key and p.get('key_file'):
        path, field = p['key_file']
        try:
            with open(os.path.expanduser(path), encoding='utf-8') as f:
                key = json.load(f).get(field, '') or ''
        except (OSError, ValueError):
            key = ''
    if not key:
        raise RuntimeError(f'{p["name"]} 的 key 未设置 (环境变量 {p["key_env"]})')
    return key


if __name__ == '__main__':   # 自检: 不打印 key 本身
    assert resolve(True) == 'deepseek' and resolve(False) == '' and resolve('minimax') == 'minimax'
    assert resolve('nope') == '' and resolve(None) == '' and resolve('') == ''
    for pid in PROVIDERS:
        try:
            print(pid, 'key 可用, 长度', len(api_key(pid)))
        except RuntimeError as e:
            print(pid, e)
    print('polish_providers selftest ok')
