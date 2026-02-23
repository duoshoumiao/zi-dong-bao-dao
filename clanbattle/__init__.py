import re
import os
import traceback
import json
from pathlib import Path
import datetime
from nonebot import get_bot
from hoshino import Service, priv
from ..login import query
from ..support_query import record_monitor
from ..util.tools import load_config, write_config, safe_send, check_client, DATA_PATH, stage_dict
from .base import *
from .model import ClanBattle
from .kpi import kpi_report
from nonebot import NoticeSession
from .sql import SubscribeDao, RecordDao, SLDao, TreeDao, ApplyDao, MemberDict
import time
import asyncio
import logging
from hoshino.typing import CQEvent, MessageSegment
# 初始化logger
logger = logging.getLogger(__name__)

# 创建服务
help_text = '''
* “+” 表示空格
【出刀监控/出刀监控2】机器人登录账号，监视出刀情况并记录
【查档线/查公会/查排名】指令+关键词或者排名
【催刀】栞栞谁没出满三刀
【当前战报】本期会战出刀情况
【我的战报 + 游戏名称】 栞栞个人出刀情况
【今日战报 + 游戏名称】 栞栞今日个人出刀情况
【昨日战报 + 游戏名称】 栞栞昨日个人出刀情况
【出刀详情 + 出刀编号】 栞栞你这刀怎么出的（出刀编号可以通过查看个人战报获得）
【今日出刀】今日出刀情况
【昨日出刀】昨日出刀情况
【启用肃正协议】数据出现异常使用即可清空所有数据（危险！！！）
【修正出刀 + 出刀编号 + （完整刀|尾刀|补偿）】修正错误的刀数记录
【状态】查看当前进度
【boss状态】看看boss里面有几个人
【预约表】栞栞谁预约了
【预约 + 数字 + （周目）+ （留言） 】预约boss, 周目和留言可不写，默认当前周目
【取消预约 + （数字）】取消预约
【清空预约 + （数字）】（仅）管理，清空预约
【查树】栞栞树上有几个人
【下树】寄，掉刀了
【全员下树】寄，掉刀了
【挂树 + 数字】失误了, 寄
【sl】记录sl
【sl?】栞栞今天有没有用过sl
【申请出刀 + 数字 + （留言） 】 申请打boss，boss死亡自动清空
【取消申请】 模拟10次挂10次，老子不打了
【清空申请出刀+数字】boss死活收不掉，先清理一波申请
【查进】查看最近的出刀和伤害记录
【查进 + 1/2/3/4/5】查看指定王的出刀记录
【状态推送】已经移除了
'''.strip()

bot = get_bot()
clanbattle_info = {}
run_group = {}
semaphore = asyncio.Semaphore(40)

# 进度记录存储路径
progress_data_dir = Path(os.path.expanduser('~/.hoshino/progress_records/'))
progress_data_dir.mkdir(parents=True, exist_ok=True)

def get_progress_file_path(gid):
    return progress_data_dir / f'group_{gid}.json'

def load_progress_records(gid):
    file_path = get_progress_file_path(gid)
    if not file_path.exists():
        return []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"加载进度记录失败: {e}")
        return []

def save_progress_records(gid, records):
    file_path = get_progress_file_path(gid)
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存进度记录失败: {e}")

sv = Service(
    name="自动报刀2",
    visible=True,
    enable_on_default=False,
    help_=help_text,
)

@sv.on_rex(r'^查进\s*([1-5])$')  # 只匹配 查进1 到 查进5
async def query_progress(bot, ev: CQEvent):
    if not ev.group_id:
        await bot.send(ev, '请在群聊中使用此功能')
        return
    
    # 获取BOSS编号
    match = ev['match']
    boss_num = int(match.group(1))  # 提取正则匹配的数字
    
    # 加载记录
    records = load_progress_records(ev.group_id)
    
    if not records:
        await bot.send(ev, '暂无出刀/伤害记录')
        return
    
    # 筛选指定BOSS的记录
    filtered_records = [
        r for r in records 
        if f"{boss_num}王" in r['message'] or f"对{boss_num}王" in r['message']
    ]
    
    if not filtered_records:
        await bot.send(ev, f'暂无{boss_num}王的出刀记录')
        return
    
    # 按时间倒序排序
    sorted_records = sorted(filtered_records, key=lambda x: x['time'], reverse=True)
    display_records = sorted_records[:10]  # 限制最多100条
    
    # 获取用户昵称
    try:
        user_info = await bot.get_group_member_info(group_id=ev.group_id, user_id=ev.user_id)
        nickname = user_info.get('nickname', str(ev.user_id))
    except:
        nickname = str(ev.user_id)
    
    # 发送合并转发消息
    await send_forward_message(bot, ev, f"{nickname}的{boss_num}王出刀记录", display_records)

async def send_forward_message(bot, ev: CQEvent, nickname, records):
    """
    发送分段合并转发消息（复用谁艾特我的逻辑）
    """
    try:
        messages = []
        
        # 添加标题节点
        messages.append({
            "type": "node",
            "data": {
                "name": nickname,
                "uin": str(bot.self_id),
                "content": f"以下是最近{len(records)}条出刀记录（按时间倒序排列）："
            }
        })
        
        # 为每条记录添加独立节点
        for i, record in enumerate(records, 1):
            # 格式化时间
            record_time = datetime.datetime.strptime(record['time'], '%Y-%m-%d %H:%M:%S.%f')
            formatted_time = record_time.strftime('%Y-%m-%d %H:%M:%S')
            
            # 构建节点内容
            content = f"{i}. {formatted_time}\n"
            content += f"   {record['message']}"
            
            messages.append({
                "type": "node",
                "data": {
                    "name": nickname,
                    "uin": str(bot.self_id),
                    "content": content
                }
            })
        
        # 发送合并转发消息
        await bot.send_group_forward_msg(group_id=ev.group_id, messages=messages)
    except Exception as e:
        logger.error(f"合并转发失败: {e}")
        # 降级为普通消息发送
        try:
            await bot.send(ev, f"以下是最近{len(records)}条出刀记录（按时间倒序排列）：")
            for msg in messages[1:]:  # 跳过标题节点
                await bot.send(ev, msg["data"]["content"])
                await asyncio.sleep(0.5)
        except Exception as fallback_error:
            logger.error(f"降级发送也失败: {fallback_error}")


@sv.on_fullmatch('出刀监控')
async def add_monitor(bot, ev):
    qq_id = ev.user_id

    if ev.message[0].type == 'at':
        if not priv.check_priv(ev, priv.ADMIN):
            await bot.send(ev, '权限不足')
            return
        else:
            qq_id = int(ev.message[0].data['qq'])

    group_id = ev.group_id
    acccountinfo = await load_config(os.path.join(DATA_PATH, 'account', f'{qq_id}.json'))

    if not acccountinfo:
        await bot.send(ev, "你没有绑定账号")
        return

    account = acccountinfo[0].get("account") or acccountinfo[0].get("viewer_id") 
    await bot.send(ev, f"正在登录账号，请耐心等待，当前监控账号为{account[:3]}******{account[-3:]}")
    
    try:
        client = await query(acccountinfo)
        if not await check_client(client):
            raise Exception("登录异常，请重试")
        # 初始化
        if group_id not in clanbattle_info:
            clanbattle_info[group_id] = ClanBattle(group_id)
        clan_info: ClanBattle = clanbattle_info[group_id]
        await clan_info.init(client, qq_id)
    except Exception as e:
        await bot.send(ev, str(e))
        return

    run_group[group_id] = ev.self_id
    loop_num = clan_info.loop_num
    clan_info.loop_check = time.time()
    await bot.send(ev, f"开始监控中, 可以发送【取消出刀监控】或者顶号退出\n#监控编号HN000{loop_num}")
    while True:
        async with semaphore:
            try:
                if loop_num != clan_info.loop_num:
                    clan_info.loop_check = False
                    raise CancleError

                clan_info.loop_check = time.time()
                # 初始化
                clan_battle_top = await clan_info.get_clanbattle_top()
                clan_info.lap_num = clan_battle_top["lap_num"]
                clan_info.rank = clan_battle_top["period_rank"]

                #换面提醒
                if clan_info.period != stage_dict[lap2stage(clan_battle_top["lap_num"])]:
                    await safe_send(bot, ev, f"阶段从{stage_dict[clan_info.period]}面到了{lap2stage(clan_battle_top['lap_num'])}面，请注意轴的切换喵")
                    clan_info.period = stage_dict[lap2stage(clan_info.lap_num)]

                change = False
                # 获取当前血量,当前王数
                for i, boss in enumerate(clan_info.boss):
                    current_boss = clan_battle_top["boss_info"][i]
                    current_hp, order, max_hp, lap_num = current_boss["current_hp"], current_boss["order_num"], current_boss["max_hp"], current_boss["lap_num"]
                    # 通知预约
                    if current_hp and (subscribe_text := await clan_info.subscribe.notify_subscribe(order, lap_num, clan_info.lap_num)):
                        clan_info.notice_subscribe.append(subscribe_text)

                    # 查看当前出刀人数
                    if fighter_num := await clan_info.refresh_fighter_num(lap_num, order):
                        msg = f"{i+1}王当前有{fighter_num}人出刀"
                        clan_info.notice_fighter.append(msg)
                        # 新增：记录到进度文件
                        records = load_progress_records(ev.group_id)
                        records.append({
                            'message': msg,
                            'time': str(datetime.datetime.now())
                        })
                        if len(records) > 100:  # 最多保留100条记录
                            records = records[-100:]
                        save_progress_records(ev.group_id, records)

                    if current_hp != boss.current_hp or lap_num != boss.lap_num:
                        change = True
                        boss.refresh(current_hp, lap_num, order, max_hp)

                await safe_send(bot, ev, "\n".join(clan_info.notice_subscribe))
                await safe_send(bot, ev, "\n".join(clan_info.notice_fighter))
                clan_info.notice_subscribe.clear()
                clan_info.notice_fighter.clear()

                if change:
                    for history in clan_battle_top["damage_history"]:
                        if history["create_time"] > clan_info.latest_time:
                            msg = f'{history["name"]}对{history["lap_num"]}周目{history["order_num"]}王造成了{history["damage"]}点伤害。'
                            clan_info.notice_dao.append(msg)
                            try:
                                group_members = await bot.get_group_member_list(group_id=group_id)
                                name_to_uid = {}
                                for member in group_members:
                                    # 优先匹配群名片，再匹配昵称
                                    card = member.get("card", "").strip()
                                    nickname = member.get("nickname", "").strip()
                                    if card:
                                        name_to_uid[card] = member["user_id"]
                                    if nickname:
                                        name_to_uid[nickname] = member["user_id"]
                                
                                # 查找用户名对应的QQ号
                                uid = name_to_uid.get(history["name"].strip())
                                if uid:
                                    # 调用取消申请的处理逻辑
                                    apply_dao = ApplyDao(group_id)
                                    apply_dao.delete_apply(uid)  # 删除该用户的申请记录
                                    logger.info(f'自动取消用户{history["name"]}(uid={uid})的出刀申请')
                            except Exception as e:
                                logger.error(f'')
                            # 新增：记录到进度文件
                            records = load_progress_records(ev.group_id)
                            records.append({
                                'message': msg,
                                'time': str(datetime.datetime.now())
                            })
                            if len(records) > 100:  # 最多保留100条记录
                                records = records[-100:]
                            save_progress_records(ev.group_id, records)
                            # 通知挂树，清空申请出刀
                            if history["kill"]:
                                await safe_send(bot, ev, clan_info.general_boss())
                                if offtree_text := await clan_info.tree.notify_tree(history["order_num"]):
                                    clan_info.notice_tree.append(offtree_text)

                                clan_info.apply.clear_apply(history["order_num"])

                    clan_info.refresh_latest_time(clan_battle_top)
                    await safe_send(bot, ev, "\n".join(clan_info.notice_dao[::-1]))
                    clan_info.notice_dao.clear()
                    await safe_send(bot, ev, "\n".join(clan_info.notice_tree))
                    clan_info.notice_tree.clear()

                clan_info.error_count = 0
                await clan_info.add_record(clan_battle_top["damage_history"], loop_num)

            except Exception as e:
                print(traceback.format_exc())
                clan_info.loop_check = False
                del run_group[group_id]

                # logger.error(traceback.format_exc())
                if loop_num != clan_info.loop_num:
                    await bot.send(ev, f"#编号HN000{loop_num}监控已关闭")
                    return

                if not await check_client(clan_info.client):
                    await bot.send(ev, "当前账号被顶号，监控已退出")
                    return

                if clan_info.error_count > 3:
                    clan_info.error_count = 0
                    await bot.send(ev, "超过最大重试次数，监控已退出")
                    return

                clan_info.loop_check = True
                clan_info.error_count += 1
                run_group[group_id] = ev.self_id
        await asyncio.sleep(1)

@sv.on_fullmatch('出刀监控2')
async def add_monitor(bot, ev):
    qq_id = ev.user_id

    if ev.message[0].type == 'at':
        if not priv.check_priv(ev, priv.ADMIN):
            await bot.send(ev, '权限不足')
            return
        else:
            qq_id = int(ev.message[0].data['qq'])

    group_id = ev.group_id
    acccountinfo = await load_config(os.path.join(DATA_PATH, 'account2', f'{qq_id}.json'))

    if not acccountinfo:
        await bot.send(ev, "你没有绑定账号")
        return

    account = acccountinfo[0].get("account") or acccountinfo[0].get("viewer_id") 
    await bot.send(ev, f"正在登录账号，请耐心等待，当前监控账号为{account[:3]}******{account[-3:]}")
    
    try:
        client = await query(acccountinfo)
        if not await check_client(client):
            raise Exception("登录异常，请重试")
        # 初始化
        if group_id not in clanbattle_info:
            clanbattle_info[group_id] = ClanBattle(group_id)
        clan_info: ClanBattle = clanbattle_info[group_id]
        await clan_info.init(client, qq_id)
    except Exception as e:
        await bot.send(ev, str(e))
        return

    run_group[group_id] = ev.self_id
    loop_num = clan_info.loop_num
    clan_info.loop_check = time.time()
    await bot.send(ev, f"开始监控中, 可以发送【取消出刀监控】或者顶号退出\n#监控编号HN000{loop_num}")
    while True:
        async with semaphore:
            try:
                if loop_num != clan_info.loop_num:
                    clan_info.loop_check = False
                    raise CancleError

                clan_info.loop_check = time.time()
                # 初始化
                clan_battle_top = await clan_info.get_clanbattle_top()
                clan_info.lap_num = clan_battle_top["lap_num"]
                clan_info.rank = clan_battle_top["period_rank"]

                #换面提醒
                if clan_info.period != stage_dict[lap2stage(clan_battle_top["lap_num"])]:
                    await safe_send(bot, ev, f"阶段从{stage_dict[clan_info.period]}面到了{lap2stage(clan_battle_top['lap_num'])}面，请注意轴的切换喵")
                    clan_info.period = stage_dict[lap2stage(clan_info.lap_num)]

                change = False
                # 获取当前血量,当前王数
                for i, boss in enumerate(clan_info.boss):
                    current_boss = clan_battle_top["boss_info"][i]
                    current_hp, order, max_hp, lap_num = current_boss["current_hp"], current_boss["order_num"], current_boss["max_hp"], current_boss["lap_num"]
                    # 通知预约
                    if current_hp and (subscribe_text := await clan_info.subscribe.notify_subscribe(order, lap_num, clan_info.lap_num)):
                        clan_info.notice_subscribe.append(subscribe_text)

                    # 查看当前出刀人数
                    if fighter_num := await clan_info.refresh_fighter_num(lap_num, order):
                        msg = f"{i+1}王当前有{fighter_num}人出刀"
                        clan_info.notice_fighter.append(msg)
                        # 新增：记录到进度文件
                        records = load_progress_records(ev.group_id)
                        records.append({
                            'message': msg,
                            'time': str(datetime.datetime.now())
                        })
                        if len(records) > 100:  # 最多保留100条记录
                            records = records[-100:]
                        save_progress_records(ev.group_id, records)

                    if current_hp != boss.current_hp or lap_num != boss.lap_num:
                        change = True
                        boss.refresh(current_hp, lap_num, order, max_hp)

                await safe_send(bot, ev, "\n".join(clan_info.notice_subscribe))
                await safe_send(bot, ev, "\n".join(clan_info.notice_fighter))
                clan_info.notice_subscribe.clear()
                clan_info.notice_fighter.clear()

                if change:
                    for history in clan_battle_top["damage_history"]:
                        if history["create_time"] > clan_info.latest_time:
                            msg = f'{history["name"]}对{history["lap_num"]}周目{history["order_num"]}王造成了{history["damage"]}点伤害。'
                            clan_info.notice_dao.append(msg)
                            # 新增：记录到进度文件
                            records = load_progress_records(ev.group_id)
                            records.append({
                                'message': msg,
                                'time': str(datetime.datetime.now())
                            })
                            if len(records) > 100:  # 最多保留100条记录
                                records = records[-100:]
                            save_progress_records(ev.group_id, records)
                            # 通知挂树，清空申请出刀
                            if history["kill"]:
                                await safe_send(bot, ev, clan_info.general_boss())
                                if offtree_text := await clan_info.tree.notify_tree(history["order_num"]):
                                    clan_info.notice_tree.append(offtree_text)

                                clan_info.apply.clear_apply(history["order_num"])

                    clan_info.refresh_latest_time(clan_battle_top)
                    await safe_send(bot, ev, "\n".join(clan_info.notice_dao[::-1]))
                    clan_info.notice_dao.clear()
                    await safe_send(bot, ev, "\n".join(clan_info.notice_tree))
                    clan_info.notice_tree.clear()

                clan_info.error_count = 0
                await clan_info.add_record(clan_battle_top["damage_history"], loop_num)

            except Exception as e:
                print(traceback.format_exc())
                clan_info.loop_check = False
                del run_group[group_id]

                # logger.error(traceback.format_exc())
                if loop_num != clan_info.loop_num:
                    await bot.send(ev, f"#编号HN000{loop_num}监控已关闭")
                    return

                if not await check_client(clan_info.client):
                    await bot.send(ev, "当前账号被顶号，监控已退出")
                    return

                if clan_info.error_count > 3:
                    clan_info.error_count = 0
                    await bot.send(ev, "超过最大重试次数，监控已退出")
                    return

                clan_info.loop_check = True
                clan_info.error_count += 1
                run_group[group_id] = ev.self_id
        await asyncio.sleep(1)

@sv.on_fullmatch('取消出刀监控')
async def delete_monitor(bot, ev):
    group_id = ev.group_id
    qq_id = ev.user_id
    if group_id in clanbattle_info:
        clan_info: ClanBattle = clanbattle_info[group_id]
        if qq_id == clan_info.qq_id or priv.check_priv(ev, priv.ADMIN):
            clan_info.loop_num += 1
        else:
            await bot.send(ev, "你不是监控人或者管理")
    else:
        await bot.send(ev, "本群未曾开过出刀监控")


@sv.on_fullmatch('状态')
async def daostate(bot, ev):
    group_id = ev.group_id
    if group_id in clanbattle_info:
        clan_info: ClanBattle = clanbattle_info[group_id]
        now = time.time()
        msg = f'当前排名：{clan_info.rank}\n监控状态：'
        if clan_info.loop_check:
            msg += '开启'
            member_info = await bot.get_group_member_info(group_id=group_id, user_id=clan_info.qq_id)
            msg += f'\n监控人为：{member_info["card"] or member_info["nickname"]}'
            msg += "(高占用)" if now - clan_info.loop_check > 30 else ""
        else:
            msg += '关闭'
        msg += "\n" + clan_info.general_boss()
        await safe_send(bot, ev, msg)

        msg = ""
        for i in range(1, 5 + 1):
            if apply_info := clan_info.apply.get_apply(i):
                msg += f"========={i}王=========\n"
                msg += f"当前有{len(apply_info)}人申请挑战boss\n"
                for i, info in enumerate(apply_info):
                    uid, apply_time, text = info
                    member_info = await bot.get_group_member_info(group_id=group_id, user_id=uid)
                    name = member_info["card"] or member_info["nickname"]
                    msg += f"->{i+1}：{name} {text} 已过去{format_time(now - apply_time)}\n"
        await safe_send(bot, ev, msg.strip())
    else:
        await bot.send(ev, "未查询到本群当前进度，请开启出刀监控")


@sv.on_fullmatch('boss状态')
async def bosstate(bot, ev):
    group_id = ev.group_id
    if group_id in clanbattle_info:
        clan_info : ClanBattle = clanbattle_info[group_id]
        now = time.time()
        msg = '监控状态：'
        if clan_info.loop_check:
            msg += '开启'
            member_info = await bot.get_group_member_info(group_id=group_id, user_id=clan_info.qq_id)
            msg += f'\n监控人为：{member_info["card"] or member_info["nickname"]}'
            msg += "(高占用)" if now - clan_info.loop_check > 30 else ""
        else:
            msg += '关闭'
        for i in range(1, 5 + 1):
            if apply_info := clan_info.apply.get_apply(i):
                msg += f"\n========={i}王=========\n"
                msg += f"当前有{len(apply_info)}人申请挑战boss\n"
                for i, info in enumerate(apply_info):
                    uid, apply_time, text = info
                    member_info = await bot.get_group_member_info(group_id=group_id, user_id=uid)
                    name = member_info["card"] or member_info["nickname"]
                    msg += f"->{i+1}：{name} {text} 已过去{format_time(now - apply_time)}\n"
            if clan_info.boss[i-1].fighter_num:
                msg += f"当前挑战人数{clan_info.boss[i-1].fighter_num}\n"
        await bot.send(ev, msg.strip())
    else:
        await bot.send(ev, "Ciallo ( ・ω )<~，请重新监控")


@sv.on_rex(r'^预约\s?(\d)(\s\d+)?(\s\S*)?$')
async def subscirbe(bot, ev):
    group_id = ev.group_id
    uid = ev.user_id
    match = ev['match']
    boss = int(match.group(1))
    lap = int(match.group(2)[1:]) if match.group(2) else 0

    if boss > 5 or boss < 1:
        await bot.send(ev, "不约，滚")
        return

    subDao = SubscribeDao(group_id)
    if text := match.group(3):
        text = text[1:]

    if subDao.add_subscribe(uid, boss, lap, text if text else " "):
        await bot.send(ev, '预约成功', at_sender=True)
    else:
        await bot.send(ev, '预约失败', at_sender=True)


@sv.on_fullmatch('预约表', only_to_me=False)
async def formsubscribe(bot, ev):
    group_id = ev.group_id
    FormSubscribe = ""
    subscribers = []
    subDao = SubscribeDao(group_id)
    for boss in range(1, 5 + 1):
        if info := subDao.get_subscriber(boss):
            for qq, lap, text in info:
                lap = f"第{lap}周目" if lap else "当前周目"
                info = await bot.get_group_member_info(group_id=ev.group_id, user_id=qq)
                name = "card" if info["card"] else "nickname"
                msg = f'{info[name]}:{text}' if text else info[name]
                msg += " " + lap
                subscribers.append(msg)
        if subscribers:
            FormSubscribe += f'\n========={boss}王=========\n' + \
                "\n".join(subscribers)
            subscribers = []

    if FormSubscribe:
        await bot.send(ev, "当前预约列表" + FormSubscribe)
    else:
        await bot.send(ev, "无人预约呢喵")


@sv.on_rex(r'^取消预约\s?(\d)$')
async def cancelsubscirbe(bot, ev):
    uid = ev.user_id
    group_id = ev.group_id
    match = ev['match']
    boss = int(match.group(1))

    if boss > 5 or boss < 1:
        await bot.send(ev, "爬爬")
        return

    for m in ev['message']:
        if m.type == 'at' and m.data['qq'] != 'all':
            if not priv.check_priv(ev, priv.ADMIN):
                await bot.send(ev, '权限不足')
                return
            else:
                uid = int(m.data['qq'])
    subDao = SubscribeDao(group_id)
    subDao.delete_subscriber(uid, boss)

    await bot.send(ev, '取消成功', at_sender=True)


@sv.on_rex(r'^清空预约\s?(\d)$')
async def cleansubscirbe(bot, ev):
    group_id = ev.group_id
    if not priv.check_priv(ev, priv.ADMIN):
        await bot.send(ev, '权限不足')
    else:
        match = ev['match']
        boss = int(match.group(1))
        if boss > 5 or boss < 1:
            await bot.send(ev, "爬爬")
            return
        subDao = SubscribeDao(group_id)
        subDao.clear_subscriber(boss)
        await bot.send(ev, '清除成功', at_sender=True)


@sv.on_fullmatch(('sl', 'SL', "Sl"))
async def addsl(bot, ev):
    group_id = ev.group_id
    sl_dao = SLDao(group_id)
    result = sl_dao.add_sl(ev.user_id)
    if result == 0:
        await bot.send(ev, 'SL已记录', at_sender=True)
    elif result == 1:
        await bot.send(ev, '今天已经SL过了', at_sender=True)
    else:
        await bot.send(ev, '数据库错误 请查看log')


@sv.on_fullmatch(('sl?', 'SL?', 'sl？', 'SL？'))
async def issl(bot, ev):
    group_id = ev.group_id
    sl_dao = SLDao(group_id)
    result = sl_dao.check_sl(ev.user_id)
    if result == 0:
        await bot.send(ev, '今天还没有使用过SL', at_sender=True)
    elif result == 1:
        await bot.send(ev, '今天已经SL过了', at_sender=True)
    else:
        await bot.send(ev, '数据库错误 请查看log')


@sv.on_rex(r"^(上|挂)树\s?(\d)\s?(.+)?$")
async def climbtree(bot, ev):
    group_id = ev.group_id
    uid = ev.user_id
    match = ev['match']
    boss = match.group(2)
    text = match.group(3)

    treeDao = TreeDao(group_id)

    if treeDao.add_tree(uid, boss, text if text else " "):
        await bot.send(ev, '上树成功', at_sender=True)
    else:
        await bot.send(ev, '上树失败', at_sender=True)


@sv.on_fullmatch('下树')
async def offtree(bot, ev):
    uid = ev.user_id
    group_id = ev.group_id

    treeDao = TreeDao(group_id)
    treeDao.delete_tree(uid)

    await bot.send(ev, '下树成功', at_sender=True)

@sv.on_fullmatch('全员下树')
async def offtree(bot, ev):
    group_id = ev.group_id

    # 获取群成员列表
    member_list = await bot.get_group_member_list(group_id=group_id)
    
    treeDao = TreeDao(group_id)
    
    # 遍历所有成员，逐个下树
    for member in member_list:
        uid = member['user_id']
        treeDao.delete_tree(uid)

    await bot.send(ev, '所有人下树成功')

@sv.on_fullmatch('查树')
async def checktree(bot, ev):
    group_id = ev.group_id
    reply = ""
    treeDao = TreeDao(group_id)
    for i in range(5):
        if info := treeDao.get_tree(i+1):
            reply += f'{i+1}王树上目前有{len(info)}人\n'
            now = time.time()
            for i, info in enumerate(info):
                uid, tree_time, text = info
                info = await bot.get_group_member_info(group_id=ev.group_id, user_id=uid)
                name = "card" if info["card"] else "nickname"
                reply += f"->{i+1}：{info[name]} {text} 已过去{format_time(now - tree_time)}\n"
    if reply:
        await bot.send(ev, reply)
    else:
        await bot.send(ev, "目前树上空空如也")


@sv.on_rex(r'^申请出刀\s?(\d)\s?(\S+)?$')
async def apply(bot, ev):
    group_id = ev.group_id
    at = re.search(r'\[CQ:at,qq=(\d*)]', str(ev.message))
    uid = at.group(1) if at else ev.user_id
    match = ev['match']

    applyDao = ApplyDao(group_id)
    boss = match.group(1)
    text = match.group(2)

    if applyDao.add_apply(uid, boss, text if text else " "):
        await bot.send(ev, "申请成功", at_sender=True)
    else:
        await bot.send(ev, "申请失败", at_sender=True)


@sv.on_fullmatch("取消申请")
async def checktree(bot, ev):
    group_id = ev.group_id
    uid = ev.user_id
    if at := re.search(r'\[CQ:at,qq=(\d*)]', str(ev.message)):
        if not priv.check_priv(ev, priv.ADMIN):
            await bot.send(ev, '权限不足')
            return
        uid = at.group(1)

    applyDao = ApplyDao(group_id)
    applyDao.delete_apply(uid)

    await bot.send(ev, '取消成功', at_sender=True)


@sv.on_fullmatch('今日出刀')
async def today_state(bot, ev):
    group_id = ev.group_id
    db = RecordDao(group_id)
    data = db.get_day_rcords(int(time.time()))
    if not data:
        await bot.send(ev, "Ciallo ( ・ω )<~，请重新监控")
    players = day_report(data)
    result = await get_stat(players, group_id)
    await bot.send(ev, result)


@sv.on_fullmatch('昨日出刀')
async def yesterday_state(bot, ev):
    group_id = ev.group_id
    db = RecordDao(group_id)
    data = db.get_day_rcords(int(time.time()) - 3600 * 24)
    if not data:
        await bot.send(ev, "Ciallo ( ・ω )<~，请重新监控")
    players = day_report(data)
    result = await get_stat(players, group_id)
    await bot.send(ev, result)


@sv.on_fullmatch('回归性原理')
async def bigfun_check(bot, ev):
    try:
        msg = await bigfun_fix(ev.group_id, RecordDao(ev.group_id))
    except Exception as e:
        msg = str(e)
    await bot.send(ev, msg)


@sv.on_fullmatch('启用肃正协议')
async def kill_all(bot, ev):
    group_id = ev.group_id
    if os.path.exists(os.path.join(clan_path, f'{group_id}', "clanbattle.db")):
        os.remove(os.path.join(clan_path, f'{group_id}', "clanbattle.db"))
        await bot.send(ev, "[WARNING]肃正协议将清理一切事物（不分敌我），期间出现任何报错均为正常现象，事后请重新开启出刀监控")


@sv.on_fullmatch('当前战报')
async def get_report(bot, ev):
    group_id = ev.group_id
    db = RecordDao(group_id)
    data = db.get_all_records()
    if not data:
        await bot.send(ev, "Ciallo ( ・ω )<~，请重新监控")
        return
    max_dao = db.get_max_dao()
    players, all_damage, all_score = clanbattle_report(data, max_dao)
    img = await get_cbreport(players, all_damage, all_score)
    await bot.send(ev, img)


@sv.on_prefix('今日战报', '昨日战报', "我的战报")
async def player_report(bot, ev):
    name = ev.message.extract_plain_text().strip()
    if (preid := ev.prefix[:2]) == "今日":
        day = 0
    elif preid == "昨日":
        day = 1
    else:
        day = 5
    group_id = ev.group_id
    db = RecordDao(group_id)
    data = db.get_player_records(name, day)
    if not data:
        await bot.send(ev, "Ciallo ( ・ω )<~，名称错误或请重新监控")
        return
    img = await get_plyerreport(data)
    await bot.send(ev, img)


@sv.on_prefix('出刀详情')
async def player_report(bot, ev):
    if id := ev.message.extract_plain_text().strip():
        if not id.isdigit():
            await bot.send(ev, "请输入正确的出刀编号")
        else:
            detail = RecordDao(ev.group_id)
            info = detail.get_history(id)
            if info:
                await bot.send(ev, await dao_detial(info))
            else:
                await bot.send(ev, "请检查你的出刀编号是否正确。")

@sv.on_rex(r'修正出刀\s?(\d+)\s?(完整刀|尾刀|补偿)?')
async def correct_dao(bot, ev):
    records = RecordDao(ev.group_id)
    info = ev["match"]
    dao_id = info.group(1)
    dao = info.group(2)
    item = 0 if dao == "完整刀" else 1 if dao == "尾刀" else 0.5
    if records.correct_dao(dao_id, item):
        await bot.send(ev, "修改成功")
    else:
        await bot.send(ev, "请检查你输入了正确的出刀编号")
    
@sv.on_fullmatch('催刀')
async def nei_gui(bot, ev):
    group_id = ev.group_id
    db = RecordDao(group_id)
    data = db.get_day_rcords(int(time.time()))
    if not data:
        await bot.send(ev, "Ciallo ( ・ω )<~，请重新监控")
    else:
        players = day_report(data)
        result = await cuidao(players, group_id)
        await bot.send(ev, result)

@sv.on_fullmatch('会战KPI', '会战kpi')
async def get_kpi(bot, ev):
    group_id = ev.group_id
    db = RecordDao(group_id)
    data = db.get_all_records()
    if not data:
        await bot.send(ev, "Ciallo ( ・ω )<~，请重新监控")
    else:
        special = await load_config(os.path.join(clan_path, f'{group_id}', "clanbattle.json"))
        special = {} if not special else special["kpi"] if "kpi" in special else {}
        players = kpi_report(data, special)
        img = await get_kpireport(players)
        await bot.send(ev, img)


@sv.on_prefix("kpi调整")
async def correct_kpi(bot, ev):
    if not priv.check_priv(ev, priv.ADMIN):
        await bot.send(ev, '权限不足')
        return
    try:
        info = ev.message.extract_plain_text().strip().split()
        id = info[0]
        score = int(info[1])
        config_file = os.path.join(
            clan_path, f'{ev.group_id}', "clanbattle.json")
        if not (config := await load_config(config_file)):
            config = {}
        if "kpi" not in config:
            config["kpi"] = {}
        config["kpi"][id] = score
        await write_config(config_file, config)
        await bot.send(ev, "设置成功")
    except:
        await bot.send(ev, "设置失败，一定是你输入了奇怪的东西，爬爬")

@sv.on_rex(r'^清空申请出刀\s?(\d)$')
async def clean_apply(bot, ev):
    group_id = ev.group_id
    if not priv.check_priv(ev, priv.ADMIN):
        await bot.send(ev, '权限不足')
    else:
        match = ev['match']
        boss = int(match.group(1))
        if boss > 5 or boss < 1:
            await bot.send(ev, "请输入1-5之间的数字")
            return
        applyDao = ApplyDao(group_id)
        applyDao.clear_apply(boss)
        await bot.send(ev, f'已清空{boss}王的出刀申请', at_sender=True)

@sv.on_fullmatch("清空kpi", "清空KPI")
async def clean_kpi(bot, ev):
    try:
        config_file = os.path.join(clan_path, f'{ev.group_id}', "clanbattle.json")
        config = await load_config(config_file)
        del config["kpi"]
        await write_config(config_file, config)
        await bot.send(ev, "清空成功")
    except:
        await bot.send(ev, "清空失败，请检查你是否设置过kpi")


@sv.on_prefix("删除kpi", "删除KPI")
async def del_kpi(bot, ev):
    try:
        id = ev.message.extract_plain_text().strip()
        config_file = os.path.join(
            clan_path, f'{ev.group_id}', "clanbattle.json")
        config = await load_config(config_file)
        del config["kpi"][id]
        await write_config(config_file, config)
        await bot.send(ev, "删除成功")
    except:
        await bot.send(ev, "删除失败，请检查此角色是否设置过kpi")

@sv.scheduled_job('cron', hour='4', minute='59', jitter=50)
async def init_cb():
    bot = get_bot()
    group_list = await bot.get_group_list()
    group_list = [group['group_id'] for group in group_list]
    for group_id in group_list:
        for db in (RecordDao(group_id), SubscribeDao(group_id), SLDao(group_id), ApplyDao(group_id), TreeDao(group_id)):
            db.refresh()

@sv.on_fullmatch("缓存运行群")
async def resatrt_remind(bot, ev):
    await write_config(run_path, run_group)
    await bot.send(ev, "成功")

@sv.on_fullmatch("提醒掉线")
async def resatrt_remind(bot, ev):
    bot = get_bot()
    for gid in (group_dict := await load_config(run_path)):
        try:
            await bot.send_group_msg(self_id=group_dict[gid], group_id=gid, message="遭遇神秘的桥本环奈偷袭，请检查出刀监控")
        except Exception as e:
            pass
    await write_config(run_path, {})

@sv.scheduled_job('cron', hour='5', minute='5') #推送5点时的名次
async def rank_and_status():
    bot = get_bot()
    for group_id in run_group:
        clan_info: ClanBattle = clanbattle_info[group_id]
        msg = f'凌晨5点时的排名为：{clan_info.rank}'
        if not clan_info.loop_check:
            msg += "，但出刀监控未开启，排名可能不准确"
        await bot.send_group_msg(group_id = group_id, message = msg)
        
   
@sv.scheduled_job('cron', minute='*/5')  # 每5分钟检查一次  
async def check_silent_offline():  
    bot = get_bot()  
    current_time = time.time()  
    offline_groups = []  
      
    for group_id in run_group:  
        if group_id in clanbattle_info:  
            clan_info = clanbattle_info[group_id]  
            # 如果心跳时间超过5分钟未更新，认为静默掉线  
            if clan_info.loop_check and (current_time - clan_info.loop_check > 300):  
                offline_groups.append(group_id)  
      
    # 向掉线的群组发送提醒  
    for group_id in offline_groups:  
        try:  
            await bot.send_group_msg(  
                self_id=run_group[group_id],   
                group_id=group_id,   
                message="检测到出刀监控可能已掉线，请检查监控状态"  
            )  
            # 从运行群组中移除，避免重复提醒  
            del run_group[group_id]  
            await write_config(run_path, run_group)  
        except Exception as e:  
            pass   
            
           

@sv.on_prefix('查档线', '查公会', '查排名')  
async def query_line(bot, ev):  
    if not priv.check_priv(ev, priv.ADMIN):   
        return await bot.send(ev, '权限不足，当前指令仅管理员可用!')  
      
    group_id = ev.group_id  
      
    # 检查是否已开启出刀监控  
    if group_id not in clanbattle_info:  
        return await bot.send(ev, "请先开启出刀监控")  
      
    # 获取已登录的 client 和相关信息  
    clan_info = clanbattle_info[group_id]  
    client = clan_info.client  
    clan_id = clan_info.clan_id  
    clan_battle_id = clan_info.clan_battle_id  
      
    goal = ev.message.extract_plain_text().strip()  
      
    try:  
        goal_list = []  
          
        # 处理数字排名查询  
        if re.match("^[0-9,]+$", goal):  
            if ',' in goal:   
                goal_list = goal.split(',')  
            else:   
                goal_list.append(goal)  
          
        # 处理默认档线查询  
        elif goal == '':  
            goal_list = [1, 11, 41, 121, 201, 401, 801, 1801, 3001, 6001]  
            await bot.send(ev, '获取数据时间较长，请稍候')  
          
        # 处理公会名搜索  
        else:  
            goal_list = []  
            await bot.send(ev, f'正在搜索行会关键词{goal}')  
              
            # 使用 client.callapi 替代 query.query  
            clan_name_search = await client.callapi('/clan/search_clan', {  
                'clan_name': goal,   
                'join_condition': 1,   
                'member_condition_range': 0,   
                'activity': 0,   
                'clan_battle_mode': 0  
            })  
              
            clan_list = ''  
            for search_clan in clan_name_search['list']:  
                clan_name = search_clan['clan_name']  
                clan_list += f'[{clan_name}]'  
              
            clan_num = len(clan_name_search['list'])  
            await bot.send(ev, f'找到{clan_num}个与关键词相关行会,超过5个的将不会查询，请精确化关键词\n{clan_list}')  
              
            clan_num = 0  
            for search_clan in clan_name_search['list']:  
                if clan_num <= 4:  
                    search_clan_id = search_clan['clan_id']  
                    if search_clan_id == 0:   
                        break  
                      
                    clan_most_info = await client.callapi('/clan/others_info', {  
                        'clan_id': search_clan_id  
                    })  
                    clan_most_info = clan_most_info['clan']['detail']['current_period_ranking']  
                      
                    if clan_most_info == 0:   
                        continue  
                      
                    goal_list.append(clan_most_info)  
                    clan_num += 1  
                else:   
                    break  
          
        if goal_list == []:   
            return await bot.send(ev, '无法获取排名，可能是官方正在结算，请等待结算后使用本功能')  
          
        # 生成图片（简化版，仅文字输出）  
        msg_list = []  
          
        for goal in goal_list:  
            goal = int(goal)  
            page = int((goal - 1) / 10)  
            in_di = goal % 10  
            if in_di == 0:   
                in_di = 10  
              
            # 获取排名数据  
            page_info = await client.callapi('/clan_battle/period_ranking', {  
                'clan_id': clan_id,  
                'clan_battle_id': clan_battle_id,  
                'period': 1,   
                'month': 0,   
                'page': page,   
                'is_my_clan': 0,   
                'is_first': 1  
            })  
              
            if page_info['period_ranking'] == []:  
                return await bot.send(ev, '当前会战排名正在结算，无法获取数据，请等待官方结算完成后再使用本功能~')  
              
            # 查找目标排名  
            num = 0  
            for rank in page_info['period_ranking']:  
                num += 1  
                if num == in_di:  
                    rank_num = rank['rank']  
                    dmg = rank['damage']  
                    mem = rank['member_num']  
                    name = rank['clan_name']  
                    l_name = rank['leader_name']  
                    g_rank = rank['grade_rank']  
                      
                    # 计算周目和进度  
                    stage = [207300000, 859700000, 4771700000, 9017700000, 999999999999]  
                    l1 = [  
                        [7200000, 9600000, 13000000, 16800000, 22500000],  
                        [9600000, 12800000, 18000000, 22800000, 30000000],  
                        [24000000, 28000000, 40800000, 45600000, 57200000],  
                        [66500000, 70000000, 85100000, 95000000, 108000000],  
                        [297500000, 315000000, 351500000, 380000000, 440000000]  
                    ]  
                    lp = [1, 6, 11, 999]  
                      
                    lap = 0  
                    boss = 0  
                    for stag in stage:  
                        lap += 1  
                        if dmg <= stag:  
                            dmg_left = dmg - stage[lap-2]  
                            break  
                      
                    l_lps = 0  
                    while dmg_left > 0:  
                        boss = 0  
                        for i in l1[lap-1]:  
                            if dmg_left - i > 0:  
                                boss += 1  
                                dmg_left -= i  
                            else:  
                                final_dmg = dmg_left  
                                dmg_left = -1  
                                break  
                        l_lps += 1  
                      
                    final_lap = lp[lap-2] + l_lps  
                    progress = (float(final_dmg/i)*100)  
                    progress = round(progress, 2)  
                      
                    msg = f'''  
排名: {rank_num}位  
公会名: {name}  
会长: {l_name}  
总伤害: {dmg}  
成员数: {mem}/30  
上期位次: {g_rank}位  
当前第 {lap} 阶段 | 第 {final_lap} 周目 {boss+1} 王 | 进度 {progress}%  
'''  
                    msg_list.append(msg.strip())  
                    break  
          
        # 发送结果  
        await bot.send(ev, '\n\n'.join(msg_list))  
          
    except Exception as e:  
        logger.exception(e)  
        await bot.send(ev, f'出现错误, 请重试:\n{str(e)}')         