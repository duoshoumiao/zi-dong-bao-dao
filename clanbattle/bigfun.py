import hashlib  
import time  
import uuid  
import httpx  
  
APPKEY = "f07288b7ef7645c7a3997baf3d208b62"  
APPSECRET = "mNnGiylYAFXbY0gPy4Zw2nG+dz1t6TYHENz61fxR3Ic="  
  
BOSS_API = "https://api.game.bilibili.com/game/player/tools/pcr/boss_daily_report"  
OVERVIEW_API = "https://api.game.bilibili.com/game/player/tools/pcr/clan_daily_report"  
MEMBER_API = "https://api.game.bilibili.com/game/player/tools/pcr/clan_daily_report_by_time"  
  
HEADERS = {  
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",  
    "Referer": "https://game.bilibili.com/tool/pcr/",  
    "Origin": "https://game.bilibili.com",  
}  
  
  
def make_sign(params: dict) -> str:  
    sorted_str = "&".join(f"{k}={params[k]}" for k in sorted(params))  
    return hashlib.md5((sorted_str + "&secret=" + APPSECRET).encode()).hexdigest()  
  
  
def build_params(**extra) -> dict:  
    params = {}  
    for k, v in extra.items():  
        if v is not None:  
            params[k] = str(v)  
    params["ts"] = str(int(time.time() * 1000))  
    params["nonce"] = str(uuid.uuid4())  
    params["appkey"] = APPKEY  
    params["sign"] = make_sign(params)  
    return params  
  
  
async def _request(url, cookie, **extra):  
    async with httpx.AsyncClient(cookies=cookie, headers=HEADERS) as client:  
        resp = await client.get(url, params=build_params(**extra), timeout=20)  
    if resp.status_code != 200:  
        raise Exception(f"HTTP {resp.status_code}，响应: {resp.text[:200]}")  
    try:  
        data = resp.json()  
    except Exception:  
        raise Exception(f"响应不是JSON，HTTP {resp.status_code}，内容: {resp.text[:200]}")  
    if data.get('code') != 0:  
        raise Exception(f"API错误 code={data.get('code')}: {data.get('message', '未知')}")  
    return data  
  
  
async def get_record(cookie):  
    """获取全部出刀记录"""  
    overview = await _request(OVERVIEW_API, cookie)  
    day_list = overview.get('data', {}).get('day_list', [])  
    if not day_list:  
        raise Exception('日期列表为空，请检查当前是否在会战期间')  
  
    all_records = []  
    for date in day_list:  
        try:  
            data = await _request(MEMBER_API, cookie, date=date, page=1, size=30)  
            records = data.get('data', {}).get('list') or []  
            all_records.extend(records)  
        except Exception:  
            pass  
    return all_records  
  
  
async def get_boss_info(cookie):  
    data = await _request(BOSS_API, cookie)  
    boss_list = data.get('data', {}).get('boss_list', [])  
    if not boss_list:  
        raise Exception('BOSS列表为空，请检查团队战工具是否有数据')  
    return {boss['boss_name']: index + 1 for index, boss in enumerate(boss_list[:5])}