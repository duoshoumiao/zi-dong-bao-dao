# clanbattle/webserver.py  
import ipaddress  
import os  
import json  
import asyncio  
import logging  
import aiohttp  
from aiohttp import web  
from ..util.tools import load_config, write_config, DATA_PATH  
import re  
from hoshino import get_bot
  
logger = logging.getLogger(__name__)  
  
clan_path = os.path.join(DATA_PATH, 'clanbattle')  
  
# Web 服务器配置  
WEB_HOST = '0.0.0.0'  
WEB_PORT = 8044  
  
# 缓存公网 IP，避免每次都请求外部 API  
_public_ip_cache = None  
  
async def get_public_ip():  
    """自动获取公网 IPv4 地址，带缓存"""  
    global _public_ip_cache  
    if _public_ip_cache:  
        return _public_ip_cache  
      
    # 优先使用只返回 IPv4 的 API  
    apis = [  
        'https://api4.ipify.org',       # 强制 IPv4  
        'https://ipv4.icanhazip.com',   # 强制 IPv4  
        'https://api.ipify.org',  
        'https://ifconfig.me/ip',  
    ]  
    for api_url in apis:  
        try:  
            async with aiohttp.ClientSession() as session:  
                async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:  
                    ip = (await resp.text()).strip()  
                    if ip:  
                        _public_ip_cache = ip  
                        logger.info(f"获取到公网IP: {ip}")  
                        return ip  
        except Exception:  
            continue  
      
    logger.warning("无法获取公网IP，使用 127.0.0.1")  
    return '127.0.0.1'  
  
  
async def get_public_url():  
    """获取对外访问的基础 URL，正确处理 IPv6"""  
    ip = await get_public_ip()  
    # 判断是否为 IPv6 地址，如果是则加方括号  
    try:  
        addr = ipaddress.ip_address(ip)  
        if addr.version == 6:  
            return f"http://[{ip}]:{WEB_PORT}"  
    except ValueError:  
        pass  
    return f"http://{ip}:{WEB_PORT}"
  
  
async def get_public_url():  
    ip = await get_public_ip()  
    try:  
        addr = ipaddress.ip_address(ip)  
        if addr.version == 6:  
            return f"http://[{ip}]:{WEB_PORT}"  
    except ValueError:  
        pass  
    return f"http://{ip}:{WEB_PORT}"
  
  
def get_config_file(group_id):  
    return os.path.join(clan_path, str(group_id), "clanbattle.json")  
  
async def auto_match_qq(group_id, game_names, existing_map):  
    """  
    对游戏名自动匹配群成员QQ号。  
    - 已有手动配置的保持不变（不覆盖）  
    - 能匹配上的自动填充  
    - 匹配不上的留空  
    """  
    result = {}  
    name_to_qq = {}  
    cleaned_name_to_qq = {}  
    try:  
        bot = get_bot()  
        if bot:  
            group_members = await bot.get_group_member_list(group_id=int(group_id))  
            for m in group_members:  
                name = m.get("card", "").strip() or m.get("nickname", "").strip()  
                if name:  
                    name_to_qq[name] = m["user_id"]  
            cleaned_name_to_qq = {clean_name(n): qq for n, qq in name_to_qq.items()}  
    except Exception as e:  
        logger.warning(f"获取群 {group_id} 成员列表失败: {e}")  
      
    for game_name in game_names:  
        if game_name in existing_map and existing_map[game_name]:  
            result[game_name] = existing_map[game_name]  
            continue  
        qq = name_to_qq.get(game_name.strip())  
        if not qq:  
            qq = cleaned_name_to_qq.get(clean_name(game_name.strip()))  
        result[game_name] = qq if qq else ''  
      
    return result    
# ========== HTML 模板 ==========  
  
HTML_TEMPLATE = '''  
<!DOCTYPE html>  
<html lang="zh-CN">  
<head>  
    <meta charset="UTF-8">  
    <meta name="viewport" content="width=device-width, initial-scale=1.0">  
    <title>催刀配置 - 群 {group_id}</title>  
    <style>  
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}  
        body {{  
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;  
            background: #f5f5f5;  
            padding: 20px;  
        }}  
        .container {{  
            max-width: 600px;  
            margin: 0 auto;  
            background: #fff;  
            border-radius: 8px;  
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);  
            padding: 24px;  
        }}  
        h1 {{  
            font-size: 20px;  
            margin-bottom: 8px;  
            color: #333;  
        }}  
        .subtitle {{  
            font-size: 14px;  
            color: #888;  
            margin-bottom: 20px;  
        }}  
        .member-row {{  
            display: flex;  
            align-items: center;  
            padding: 10px 0;  
            border-bottom: 1px solid #eee;  
        }}  
        .member-row:last-child {{ border-bottom: none; }}  
        .member-name {{  
            flex: 1;  
            font-size: 15px;  
            color: #333;  
            min-width: 0;  
            overflow: hidden;  
            text-overflow: ellipsis;  
            white-space: nowrap;  
            padding-right: 12px;  
        }}  
        .member-input {{  
            width: 180px;  
            padding: 6px 10px;  
            border: 1px solid #ddd;  
            border-radius: 4px;  
            font-size: 14px;  
            outline: none;  
            transition: border-color 0.2s;  
        }}  
        .member-input:focus {{ border-color: #4a90d9; }}  
        .btn-save {{  
            display: block;  
            width: 100%;  
            margin-top: 20px;  
            padding: 12px;  
            background: #4a90d9;  
            color: #fff;  
            border: none;  
            border-radius: 6px;  
            font-size: 16px;  
            cursor: pointer;  
            transition: background 0.2s;  
        }}  
        .btn-save:hover {{ background: #357abd; }}  
        .btn-save:disabled {{ background: #aaa; cursor: not-allowed; }}  
        .msg {{  
            margin-top: 12px;  
            padding: 10px;  
            border-radius: 4px;  
            font-size: 14px;  
            display: none;  
        }}  
        .msg.success {{ display: block; background: #e6f7e6; color: #2e7d32; }}  
        .msg.error {{ display: block; background: #fdecea; color: #c62828; }}  
        .empty {{  
            text-align: center;  
            color: #999;  
            padding: 40px 0;  
        }}  
    </style>  
</head>  
<body>  
    <div class="container">  
        <h1>催刀 QQ 映射配置</h1>  
        <div class="subtitle">群号：{group_id}</div>  
        <div id="msg" class="msg"></div>  
        <form id="form" method="POST">  
            {member_rows}  
            {save_button}  
        </form>  
    </div>  
    <script>  
        const form = document.getElementById('form');  
        if (form) {{  
            form.addEventListener('submit', async (e) => {{  
                e.preventDefault();  
                const btn = form.querySelector('button');  
                btn.disabled = true;  
                btn.textContent = '保存中...';  
                const msgEl = document.getElementById('msg');  
                msgEl.className = 'msg';  
                msgEl.style.display = 'none';  
  
                const formData = new FormData(form);  
                const data = {{}};  
                for (const [key, value] of formData.entries()) {{  
                    if (value.trim()) {{  
                        data[key] = value.trim();  
                    }}  
                }}  
  
                try {{  
                    const resp = await fetch(window.location.href, {{  
                        method: 'POST',  
                        headers: {{ 'Content-Type': 'application/json' }},  
                        body: JSON.stringify(data)  
                    }});  
                    const result = await resp.json();  
                    if (result.ok) {{  
                        msgEl.className = 'msg success';  
                        msgEl.textContent = '保存成功！共配置了 ' + result.count + ' 个映射。';  
                    }} else {{  
                        msgEl.className = 'msg error';  
                        msgEl.textContent = '保存失败：' + (result.error || '未知错误');  
                    }}  
                }} catch (err) {{  
                    msgEl.className = 'msg error';  
                    msgEl.textContent = '请求失败：' + err.message;  
                }} finally {{  
                    btn.disabled = false;  
                    btn.textContent = '保存';  
                }}  
            }});  
        }}  
    </script>  
</body>  
</html>  
'''  
  
  
def render_member_row(game_name, qq_value):  
    escaped_name = game_name.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')  
    qq_str = str(qq_value) if qq_value else ''  
    return f'''  
        <div class="member-row">  
            <div class="member-name" title="{escaped_name}">{escaped_name}</div>  
            <input class="member-input" type="text" name="{escaped_name}"  
                   value="{qq_str}" placeholder="输入QQ号" pattern="[0-9]*">  
        </div>'''  
  
  
# ========== 路由处理 ==========  
  
async def handle_get(request):  
    group_id = request.match_info['group_id']  
    config_file = get_config_file(group_id)  
  
    if not os.path.exists(config_file):  
        return web.Response(  
            text=HTML_TEMPLATE.format(  
                group_id=group_id,  
                member_rows='<div class="empty">该群尚未开启出刀监控，暂无成员数据。</div>',  
                save_button=''  
            ),  
            content_type='text/html'  
        )  
  
    config = await load_config(config_file)  
    members = config.get("member", {})  
    cuidao_qq_map = config.get("cuidao_qq_map", {})  
  
    if not members:  
        member_rows = '<div class="empty">成员列表为空，请先开启出刀监控获取成员。</div>'  
        save_button = ''  
    else:  
        auto_map = await auto_match_qq(group_id, list(members.keys()), cuidao_qq_map)  
        rows = []  
        for game_name in sorted(members.keys()):  
            qq = auto_map.get(game_name, '')  
            rows.append(render_member_row(game_name, qq))
        member_rows = '\n'.join(rows)  
        save_button = '<button class="btn-save" type="submit">保存</button>'  
  
    html = HTML_TEMPLATE.format(  
        group_id=group_id,  
        member_rows=member_rows,  
        save_button=save_button  
    )  
    return web.Response(text=html, content_type='text/html')  
  
  
async def handle_post(request):  
    group_id = request.match_info['group_id']  
    config_file = get_config_file(group_id)  
  
    if not os.path.exists(config_file):  
        return web.json_response({"ok": False, "error": "配置文件不存在"})  
  
    try:  
        data = await request.json()  
    except Exception:  
        return web.json_response({"ok": False, "error": "请求数据格式错误"})  
  
    config = await load_config(config_file)  
    members = config.get("member", {})  
  
    cuidao_qq_map = {}  
    for game_name, qq_str in data.items():  
        if game_name in members and str(qq_str).isdigit():  
            cuidao_qq_map[game_name] = int(qq_str)  
  
    config["cuidao_qq_map"] = cuidao_qq_map  
    await write_config(config_file, config)  
  
    logger.info(f"群 {group_id} 催刀QQ映射已更新，共 {len(cuidao_qq_map)} 条")  
    return web.json_response({"ok": True, "count": len(cuidao_qq_map)})  
  
  
# ========== 启动 Web 服务器 ==========  
  
app = web.Application()  
app.router.add_get('/cuidao/{group_id}', handle_get)  
app.router.add_post('/cuidao/{group_id}', handle_post)  
  
_runner = None  
  
async def start_web_server(host=WEB_HOST, port=WEB_PORT):  
    global _runner  
    _runner = web.AppRunner(app)  
    await _runner.setup()  
    site = web.TCPSite(_runner, host, port)  
    await site.start()  
    # 启动时预获取一次公网 IP 并缓存  
    ip = await get_public_ip()  
    logger.info(f"催刀配置 Web 服务器已启动: http://{ip}:{port}")  
  
async def stop_web_server():  
    global _runner  
    if _runner:  
        await _runner.cleanup()  
        _runner = None
        
     
       