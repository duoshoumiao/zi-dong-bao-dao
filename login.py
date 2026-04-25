import asyncio
import os
import httpx
import traceback
from hoshino import Service
from nonebot import get_bot, on_command, on_notice, logger
from .pcrclient import pcrclient, bsdkclient
from asyncio import Lock
from .util.tools import DATA_PATH, check_client, write_config, load_config
import re as re_module  
from .playerpref import decryptxml, decrypt_access_key
# 手动过码相关  
gt_wait = 90  # 手动过码等待时限（秒）  
captcha_lck = asyncio.Lock()  
manual_captch_result = None
sv_help = '【绑定账号1+账号+密码】加号为空格\n【渠绑定账号+login_id+token】加号为空格\n也可以直接发送 v2.playerprefs 文件自动绑定渠道服'

sv = Service('你只需要好好出刀2', help_=sv_help, visible=True, enable_on_default=False)


@sv.on_fullmatch('绑定账号帮助', only_to_me=False)
async def send_jjchelp(bot, ev):
    await bot.send_private_msg(user_id=ev.user_id, message=sv_help)

account_path = os.path.join(DATA_PATH, 'account')
bind_lck = Lock()
qu_bind_lck = Lock()
bot = get_bot()
client = None
client_cache = {}

captcha_header = {"Content-Type": "application/json",
                  "User-Agent": "pcrjjc2/1.0.0"}

async def captchaVerifier(*args):
    gt = args[0]
    challenge = args[1]
    userid = args[2]
    async with httpx.AsyncClient(timeout=30) as AsyncClient:
        try:
            res = await AsyncClient.get(url=f"https://pcrd.tencentbot.top/geetest_renew?captcha_type=1&challenge={challenge}&gt={gt}&userid={userid}&gs=1", headers=captcha_header)
            res = res.json()
            uuid = res["uuid"]
            ccnt = 0
            while (ccnt := ccnt + 1) < 10:
                res = await AsyncClient.get(url=f"https://pcrd.tencentbot.top/check/{uuid}", headers=captcha_header)
                res = res.json()

                if "queue_num" in res:
                    tim = min(int(res['queue_num']), 3) * 10
                    logger.info(f"过码排队，当前有{res['queue_num']}个在前面，等待{tim}s")
                    await asyncio.sleep(tim)
                    continue

                info = res["info"]
                if 'validate' in info:
                    return info["challenge"], info["gt_user_id"], info["validate"]

                if res["info"] in ["fail", "url invalid"]:
                    raise Exception(f"自动过码失败")

                if res["info"] == "in running":
                    logger.info(f"正在过码。等待5s")
                    await asyncio.sleep(5)

            raise Exception(f"自动过码多次失败")

        except Exception as e:
            raise Exception(f"自动过码异常，{e}")

async def manual_captch(challenge: str, gt: str, user_id: str, group_id: int, bili_account: str):  
    """向群聊发送验证链接，等待群友提交验证结果"""  
    global manual_captch_result, captcha_lck  
    manual_captch_result = None  # 重置结果  
      
    url = f"https://help.tencentbot.top/geetest/?captcha_type=1&challenge={challenge}&gt={gt}&userid={user_id}&gs=1"  
    await bot.send_group_msg(  
        group_id=group_id,  
        message=f'账号{bili_account}登录触发验证码，自动过码失败。\n请在{gt_wait}秒内完成以下链接中的验证，将第1个方框的内容复制，并加上"bdval "前缀在群里发送\n示例：bdval 123456789'  
    )  
    await bot.send_group_msg(group_id=group_id, message=url)  
      
    if not captcha_lck.locked():  
        await captcha_lck.acquire()  
      
    try:  
        await asyncio.wait_for(captcha_lck.acquire(), gt_wait)  
        captcha_lck.release()  
        if manual_captch_result:  
            return (challenge, user_id, manual_captch_result)  
        else:  
            raise RuntimeError("手动过码结果为空")  
    except asyncio.TimeoutError:  
        await bot.send_group_msg(group_id=group_id, message="手动过码超时，验证失败")  
        raise RuntimeError("手动过码获取结果超时")  
    except Exception as e:  
        await bot.send_group_msg(group_id=group_id, message=f'手动过码异常：{e}')  
        raise e

async def query(acccount_info, is_force=False, group_id=None):  
    try:  
        acccount_info = acccount_info[0].copy()  
        player = acccount_info.get('account', 0) or acccount_info.get('uid')  
        if player in client_cache and not is_force:  
            client = client_cache[player]  
            if await check_client(client):  
                return client  
          
        # 如果提供了 group_id，创建带手动过码回退的 verifier  
        if group_id:  
            async def captchaVerifier_with_fallback(*args):    
                gt, challenge, userid = args[0], args[1], args[2]    
                try:    
                    return await asyncio.wait_for(captchaVerifier(gt, challenge, userid), timeout=60)    
                except asyncio.TimeoutError:    
                    logger.error(f'自动过码超过60秒，转手动过码')    
                    account_display = str(player)    
                    if len(account_display) > 6:    
                        account_display = account_display[:3] + "***" + account_display[-3:]    
                    return await manual_captch(challenge, gt, userid, group_id, account_display)    
                except Exception as e:    
                    logger.error(f'自动过码失败: {e}，尝试群聊手动过码')    
                    account_display = str(player)    
                    if len(account_display) > 6:    
                        account_display = account_display[:3] + "***" + account_display[-3:]    
                    return await manual_captch(challenge, gt, userid, group_id, account_display)
            verifier = captchaVerifier_with_fallback  
        else:  
            verifier = captchaVerifier  
          
        client = pcrclient(bsdkclient(acccount_info, verifier))  
        await client.login()  
        if await check_client(client):  
            client_cache[player] = client  
            return client  
        raise Exception(f"登录失败，请重试")  
    except Exception as e:  
        raise Exception(f"未知错误：{e}")


  
@on_command("渠绑定账号")  
async def bind_support_qu(session):  
    content = session.ctx['message'].extract_plain_text().split()  
    qq_id = session.ctx['user_id']  
  
    if len(content) == 3:  
        # 格式: 渠绑定账号 login_id token  
        login_id = content[1]  
        password = content[2]  
    elif len(content) == 4:  
        # 格式: 渠绑定账号 login_id <加密token1> <加密token2>  
        login_id = content[1]  
        password = decrypt_access_key(f"{content[2]} {content[3]}")  
    else:  
        await bot.send_private_msg(user_id=qq_id, message=sv_help)  
        return  
  
    acccount = {  
        'platform': 4,  
        'channel': 1,  
        'qudao': 1,  
        'uid': login_id,  
        'access_key': password,  
    }  
    try:  
        client = await query([acccount.copy()], True)  
        if await check_client(client):  
            await write_config(os.path.join(account_path, f'{qq_id}.json'), [acccount])  
            await bot.send_private_msg(user_id=qq_id, message="渠道服绑定成功")  
        else:  
            await bot.send_private_msg(user_id=qq_id, message="渠道服绑定失败，请检查数据是否完整")  
    except Exception as e:  
        logger.info(traceback.format_exc())  
        await bot.send_private_msg(user_id=qq_id, message="渠道服绑定失败：" + str(e))
        
@on_command("#绑定账号")
async def bind_support(session):
    acccount = {'platform': 2, 'channel': 1, }
    content = session.ctx['message'].extract_plain_text().split()
    qq_id = session.ctx['user_id']
    if len(content) != 3:
        await bot.send_private_msg(user_id=qq_id, message=sv_help)
    else:
        acccount["account"] = content[1]
        acccount['password'] = content[2]
        try:
            client = await query([acccount.copy()], True)
            if await check_client(client):
                await write_config(os.path.join(account_path, f'{qq_id}.json'), [acccount])
                await bot.send_private_msg(user_id=qq_id, message="绑定成功")
        except Exception as e:
            logger.info(traceback.format_exc())
            await bot.send_private_msg(user_id=qq_id, message="绑定失败" + str(e))

@on_notice("offline_file")  
async def qu_bind_file(session):  
    """通过QQ离线文件上传 v2.playerprefs 或 base.track 自动绑定渠道服账号"""  
    file = session.ctx.get('file', {})  
    # 防止过大文件  
    if file.get("size", 0) // 1024 // 1024 >= 1:  
        return  
  
    file_name = file.get("name", "")  
    if "v2.playerprefs" not in file_name and "base.track" not in file_name:  
        return  
  
    qq_id = session.ctx['user_id']  
    try:  
        async with httpx.AsyncClient() as AsyncClient:  
            res = await AsyncClient.get(url=file["url"])  
            content = res.content.decode()  
  
        if "bilibili.priconne" in file_name:  
            # 渠道服 v2.playerprefs  
            udid, viewer_id = decryptxml(content)  
            acccount = {  
                'platform': 4,  
                'channel': 1,  
                'qudao': 1,  
                'uid': viewer_id,  
                'access_key': udid,  
            }  
        elif "base.track" in file_name:  
            # base.track 文件  
            acccount = None  
            for m in re_module.finditer(r'<string name="(.*)">(\d{22})</string>', content):  
                acccount = {  
                    'platform': 4,  
                    'channel': 1,  
                    'qudao': 1,  
                    'uid': m.groups()[1],  
                    'access_key': '',  
                }  
                break  
            if not acccount:  
                await bot.send_private_msg(user_id=qq_id, message="未在文件中找到有效账号信息")  
                return  
        else:  
            return  
  
        await bot.send_private_msg(user_id=qq_id, message="接受文件成功，正在验证登录...")  
        client = await query([acccount.copy()], True)  
        if await check_client(client):  
            await write_config(os.path.join(account_path, f'{qq_id}.json'), [acccount])  
            await bot.send_private_msg(user_id=qq_id, message="文件绑定成功")  
        else:  
            await bot.send_private_msg(user_id=qq_id, message="文件绑定失败，登录验证未通过")  
    except Exception as e:  
        logger.info(traceback.format_exc())  
        await bot.send_private_msg(user_id=qq_id, message="文件绑定失败：" + str(e))