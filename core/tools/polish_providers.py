"""
二次整理可选的在线 LLM 服务商 (客户端托盘菜单 + 服务端调用共用这一份).

加服务商: 在 PROVIDERS 里加一项. 要求 OpenAI 兼容的 /chat/completions 接口, 且能关掉"思考" (否则一句要等好几秒).
key 只从环境变量读; 某些服务商允许回退读它自家命令行工具的配置文件 (key_file, 只读, 不复制到别处).
实测延迟 (2026-09-23, 关思考): DeepSeek V4 Flash 0.4-1.2s; MiniMax M3 1.2-1.3s; MiniMax M2.7 关不掉思考 3-6s, 不收录.
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
}
DEFAULT = 'deepseek'


def resolve(value) -> str:
    """客户端传来的 polish 字段 -> 服务商 id; '' = 不整理. 兼容旧版的 True/False."""
    if value is True:
        return DEFAULT
    if isinstance(value, str) and value in PROVIDERS:
        return value
    return ''


def api_key(pid: str) -> str:
    p = PROVIDERS[pid]
    key = os.environ.get(p['key_env'], '')
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
