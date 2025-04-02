# -*- coding: utf-8 -*-

"""
Author: Cluic
Modified by: iwyxdxl
"""

from . import uiautomation as uia
from .languages import *
from .utils import *
from .elements import *
from .errors import *
from .color import *
import time
import os
import re
import threading
import time
import traceback
try:
    from typing import Literal
except:
    from typing_extensions import Literal

# 统一响应类，提供标准化的返回格式
class WxResponse(dict):
    def __init__(self, status: str, msg: str, data: dict = None):
        super().__init__(status=status, msg=msg, data=data)

    def __str__(self):
        return str(dict(status=self['status'], message=self['msg'], data=self['data']))
    
    def __repr__(self):
        return self.__str__()

    def __bool__(self):
        return self.is_success
    
    @property
    def is_success(self):
        return self['status'] == '成功'

    @classmethod
    def success(cls, msg=None, data: dict = None):
        return cls(status="成功", msg=msg, data=data)

    @classmethod
    def failure(cls, msg: str, data: dict = None):
        return cls(status="失败", msg=msg, data=data)
    
# 监听器基类，实现后台消息监听
class Listener:
    def _listener_start(self):
        wxlog.debug('开始监听')
        self._listener_is_listening = True
        self._listener_messages = {}  # 存储监听到的消息
        self._lock = threading.Lock()  # 线程锁，保护共享数据
        self._listener_stop_event = threading.Event()  # 停止事件
        self._listener_thread = threading.Thread(target=self._listener_listen, daemon=True)
        self._listener_thread.start()

    def _listener_listen(self):
        if not hasattr(self, 'listen') or not self.listen:
            self.listen = {}
        while not self._listener_stop_event.is_set():
            try:
                self._get_listen_messages()
            except:
                wxlog.debug(f'监听消息失败：{traceback.format_exc()}')
            time.sleep(1)  # 监听间隔

    def _listener_stop(self):
        self._listener_is_listening = False
        self._listener_stop_event.set()
        self._listener_thread.join()

    def _get_listen_messages(self):
        """获取监听的新消息"""
        temp_listen = self.listen.copy()
        for who in temp_listen:
            chat = temp_listen.get(who, None)
            try:
                if chat is None or not chat.UiaAPI.Exists(0.1):
                    continue
                
                with self._lock:
                    # 检查并获取新消息
                    msg = chat.GetNewMessage(
                        savepic=chat.savepic, 
                        savefile=chat.savefile, 
                        savevoice=chat.savevoice
                    )
                    
                    # 检查是否有真正的新消息
                    if msg:
                        # 过滤掉可能重复的消息ID
                        if not hasattr(chat, 'processed_msg_ids'):
                            chat.processed_msg_ids = set()
                        
                        # 过滤已处理的消息
                        new_msgs = []
                        for m in msg:
                            if m.id not in chat.processed_msg_ids:
                                chat.processed_msg_ids.add(m.id)
                                new_msgs.append(m)
                        
                        # 限制已处理ID集合的大小，避免内存泄漏
                        if len(chat.processed_msg_ids) > 1000:
                            chat.processed_msg_ids = set(list(chat.processed_msg_ids)[-500:])
                        
                        # 只添加真正的新消息
                        if new_msgs:
                            wxlog.debug(f"获取到 {len(new_msgs)} 条新消息")
                            if chat not in self._listener_messages:
                                self._listener_messages[chat] = new_msgs
                            else:
                                self._listener_messages[chat].extend(new_msgs)
                        
            except Exception as e:
                wxlog.debug(f'监听消息处理异常：{str(e)}\n{traceback.format_exc()}')

class WeChat(WeChatBase, Listener):
    VERSION: str = '3.9.11.17'
    lastmsgid: str = None
    listen: dict = dict()
    SessionItemList: list = []

    def __init__(self, language='cn', debug=False):
        """微信UI自动化实例

        Args:
            language (str, optional): 微信客户端语言版本, 可选: cn简体中文  cn_t繁体中文  en英文, 默认cn, 即简体中文
        """
        self.UiaAPI = uia.WindowControl(ClassName='WeChatMainWndForPC', searchDepth=1)
        set_debug(debug)
        self.language = language
        # self._checkversion()
        self._show()
        MainControl1 = [i for i in self.UiaAPI.GetChildren() if not i.ClassName][0]
        MainControl2 = MainControl1.GetFirstChildControl()
        # 三个布局，导航栏(A)、聊天列表(B)、聊天框(C)
        self.NavigationBox, self.SessionBox, self.ChatBox = MainControl2.GetChildren()
        
        # 初始化导航栏
        self.A_MyIcon = self.NavigationBox.ButtonControl()
        self.A_ChatIcon = self.NavigationBox.ButtonControl(Name=self._lang('聊天'))
        self.A_ContactsIcon = self.NavigationBox.ButtonControl(Name=self._lang('通讯录'))
        self.A_FavoritesIcon = self.NavigationBox.ButtonControl(Name=self._lang('收藏'))
        self.A_FilesIcon = self.NavigationBox.ButtonControl(Name=self._lang('聊天文件'))
        self.A_MomentsIcon = self.NavigationBox.ButtonControl(Name=self._lang('朋友圈'))
        self.A_MiniProgram = self.NavigationBox.ButtonControl(Name=self._lang('小程序面板'))
        self.A_Phone = self.NavigationBox.ButtonControl(Name=self._lang('手机'))
        self.A_Settings = self.NavigationBox.ButtonControl(Name=self._lang('设置及其他'))
        
        # 初始化聊天列表，以B开头
        self.B_Search = self.SessionBox.EditControl(Name=self._lang('搜索'))
        
        # 初始化聊天栏，以C开头
        self.C_MsgList = self.ChatBox.ListControl(Name=self._lang('消息'))
        
        self.nickname = self.A_MyIcon.Name
        msgs_ = self.GetAllMessage()
        self.usedmsgid = [i[-1] for i in msgs_]
        
        # 初始化消息队列
        self._message_queue = []
        self._file_queue = []
        self._queue_lock = threading.Lock()
        self._sender_stop_event = threading.Event()
        self._sender_thread = threading.Thread(target=self._background_sender, daemon=True)
        self._sender_thread.start()

        # 启动后台监听线程
        self._listener_start()
        
        print(f'初始化成功，获取到已登录窗口：{self.nickname}')
    
    def _checkversion(self):
        self.HWND = FindWindow(classname='WeChatMainWndForPC')
        wxpath = GetPathByHwnd(self.HWND)
        wxversion = GetVersionByPath(wxpath)
        if wxversion != self.VERSION:
            Warnings.lightred(self._lang('版本不一致', 'WARNING').format(wxversion, self.VERSION), stacklevel=2)
            return False
    
    
    def _show(self):
        self.HWND = FindWindow(classname='WeChatMainWndForPC')
        win32gui.ShowWindow(self.HWND, 1)
        win32gui.SetWindowPos(self.HWND, -1, 0, 0, 0, 0, 3)
        win32gui.SetWindowPos(self.HWND, -2, 0, 0, 0, 0, 3)
        self.UiaAPI.SwitchToThisWindow()

    def _refresh(self):
        self.UiaAPI.SendKeys('{Ctrl}{Alt}w')
        self.UiaAPI.SendKeys('{Ctrl}{Alt}w')
        self._show()

    def _get_friend_details(self):
        params = ['昵称：', '微信号：', '地区：', '备注', '电话', '标签', '共同群聊', '个性签名', '来源', '朋友权限', '描述', '实名', '企业']
        info = {}
        controls = GetAllControlList(self.ChatBox)
        for _, i in enumerate(controls):
            rect = i.BoundingRectangle
            text = i.Name
            if text in params or (rect.width() == 57 and rect.height() == 20):
                info[text.replace('：', '')] = controls[_+1].Name
        if '昵称' not in info:
            info['备注'] = ''
            info['昵称'] = controls[0].Name
        wxlog.debug(f'获取到好友详情：{info}')
        return info
    
    def _goto_first_friend(self):
        def find_letter_tag(self):
            items = self.SessionBox.ListControl().GetChildren()
            for index, item in enumerate(items[:-1]):
                if item.TextControl(RegexName='^[A-Z]$').Exists(0):
                    # print('>>> bingo!\n')
                    # GetAllControl(item)
                    return items[index+1]
        while True:
            item = find_letter_tag(self)
            if item is not None:
                self.SessionBox.WheelDown(wheelTimes=3)
                item.Click(simulateMove=False)
                break
            self.SessionBox.WheelDown(wheelTimes=3, interval=0)

    def GetFriendDetails(self, n=None, timeout=0xFFFFF):
        """获取所有好友详情信息
        
        Args:
            n (int, optional): 获取前n个好友详情信息, 默认为None，获取所有好友详情信息
            timeout (int, optional): 获取超时时间（秒），超过该时间则直接返回结果

        Returns:
            list: 所有好友详情信息
            
        注：1. 该方法运行时间较长，约0.5~1秒一个好友的速度，好友多的话可将n设置为一个较小的值，先测试一下
            2. 如果遇到企业微信的好友且为已离职状态，可能导致微信卡死，需重启（此为微信客户端BUG）
            3. 该方法未经过大量测试，可能存在未知问题，如有问题请微信群内反馈
        """
        t0 = time.time()
        self.SwitchToContact()
        self._goto_first_friend()
        details = []
        while True:
            if time.time() - t0 > timeout:
                wxlog.debug('获取好友详情超时，返回结果')
                return details
            _detail = self._get_friend_details()
            if details and _detail == details[-1]:
                return details
            details.append(_detail)
            self.SessionBox.SendKeys('{DOWN}')
            if n and len(details) >= n:
                return details

            
    def GetSessionAmont(self, SessionItem):
        """获取聊天对象名和新消息条数
        
        Args:
            SessionItem (uiautomation.ListItemControl): 聊天对象控件
            
        Returns:
            sessionname (str): 聊天对象名
            amount (int): 新消息条数
        """
        matchobj = re.search('\d+条新消息', SessionItem.Name)
        amount = 0
        if matchobj:
            try:
                amount = int([i for i in SessionItem.GetFirstChildControl().GetChildren() if type(i) == uia.TextControl][0].Name)
            except:
                pass
        sessionname = SessionItem.Name if SessionItem.ButtonControl().Name == 'SessionListItem' else SessionItem.ButtonControl().Name
        return sessionname, amount
    
    def CheckNewMessage(self):
        """是否有新消息"""
        self._show()
        return IsRedPixel(self.A_ChatIcon)
    
    def GetNextNewMessage(self, savepic=False, savefile=False, savevoice=False, timeout=10):
        """获取下一个新消息"""
        msgs_ = self.GetAllMessage()
        msgids = [i[-1] for i in msgs_]

        if not self.usedmsgid:
            self.usedmsgid = msgids
        
        newmsgids = [i for i in msgids if i not in self.usedmsgid]
        oldmsgids = [i for i in self.usedmsgid if i in msgids]
        if newmsgids and oldmsgids:
            MsgItems = self.C_MsgList.GetChildren()
            msgids = [''.join([str(i) for i in i.GetRuntimeId()]) for i in MsgItems]
            new = []
            for i in range(len(msgids)-1, -1, -1):
                if msgids[i] in self.usedmsgid:
                    new = msgids[i+1:]
                    break
            NewMsgItems = [
                i for i in MsgItems 
                if ''.join([str(i) for i in i.GetRuntimeId()]) in new
                and i.ControlTypeName == 'ListItemControl'
            ]
            if NewMsgItems:
                wxlog.debug('获取当前窗口新消息')
                msgs = self._getmsgs(NewMsgItems, savepic, savefile, savevoice)
                self.usedmsgid = msgids
                return {self.CurrentChat(): msgs}

        if self.CheckNewMessage():
            wxlog.debug('获取其他窗口新消息')
            t0 = time.time()
            while True:
                if time.time() - t0 > timeout:
                    wxlog.debug('获取新消息超时')
                    return {}
                self.A_ChatIcon.DoubleClick(simulateMove=False)
                sessiondict = self.GetSessionList(newmessage=True)
                if sessiondict:
                    break
            for session in sessiondict:
                self.ChatWith(session)
                NewMsgItems = self.C_MsgList.GetChildren()[-sessiondict[session]:]
                msgs = self._getmsgs(NewMsgItems, savepic, savefile, savevoice)
                msgs_ = self.GetAllMessage()
                self.usedmsgid = [i[-1] for i in msgs_]
                return {session:msgs}
        else:
            wxlog.debug('没有新消息')
            return {}
    
    def GetAllNewMessage(self, max_round=10):
        """获取所有新消息
        
        Args:
            max_round (int): 最大获取次数  * 这里是为了避免某几个窗口一直有新消息，导致无法停止
        """
        newmessages = {}
        for _ in range(max_round):
            newmsg = self.GetNextNewMessage()
            if newmsg:
                for session in newmsg:
                    if session not in newmessages:
                        newmessages[session] = []
                    newmessages[session].extend(newmsg[session])
            else:
                break
        return newmessages
    
    def GetSessionList(self, reset=False, newmessage=False):
        """获取当前聊天列表中的所有聊天对象
        
        Args:
            reset (bool): 是否重置SessionItemList
            newmessage (bool): 是否只获取有新消息的聊天对象
            
        Returns:
            SessionList (dict): 聊天对象列表，键为聊天对象名，值为新消息条数
        """
        self.SessionItem = self.SessionBox.ListItemControl()
        if reset:
            self.SessionItemList = []
        SessionList = {}
        for i in range(100):
            if self.SessionItem.BoundingRectangle.width() != 0:
                try:
                    name, amount = self.GetSessionAmont(self.SessionItem)
                except:
                    break
                if name not in self.SessionItemList:
                    self.SessionItemList.append(name)
                if name not in SessionList:
                    SessionList[name] = amount
            self.SessionItem = self.SessionItem.GetNextSiblingControl()
            if not self.SessionItem:
                break
            
        if newmessage:
            return {i:SessionList[i] for i in SessionList if SessionList[i] > 0}
        return SessionList
    
    def GetSession(self):
        """获取当前聊天列表中的所有聊天对象

        Returns:
            SessionElement: 聊天对象列表

        Example:
            >>> wx = WeChat()
            >>> sessions = wx.GetSession()
            >>> for session in sessions:
            ...     print(f"聊天对象名称: {session.name}")
            ...     print(f"最后一条消息时间: {session.time}")
            ...     print(f"最后一条消息内容: {session.content}")
            ...     print(f"是否有新消息: {session.isnew}", end='\n\n')
        """
        sessions = self.SessionBox.ListControl()
        return [SessionElement(i) for i in sessions.GetChildren()]
    
    def ChatWith(self, who, timeout=3, exact=False):
        '''打开某个聊天框
        
        Args:
            who ( str ): 要打开的聊天框好友名;  * 最好完整匹配，不完全匹配只会选取搜索框第一个
            timeout ( num, optional ): 超时时间，默认3秒
            exact ( bool, optional ): 是否精确匹配好友名，默认False

        Returns:
            chatname ( str ): 匹配值第一个的完整名字
        '''
        if WxParam.MOUSE_MOVE:
            self._show()
        sessiondict = self.GetSessionList(True)
        if who in list(sessiondict.keys())[:-1]:
            self.SessionBox.ListItemControl(RegexName=re.escape(who)).Click(move=WxParam.MOUSE_MOVE, simulateMove=False, return_pos=False)
            return who
        self.UiaAPI.ShortcutSearch(click=False)
        self.B_Search.Click(move=WxParam.MOUSE_MOVE, simulateMove=False, return_pos=False)
        self.B_Search.Input(who)

        search_result = self.SessionBox.ListControl(RegexName='.*?IDS_FAV_SEARCH_RESULT.*?')
        
        t0 = time.time()
        while time.time() -t0 < timeout:
            results = []
            search_result_items = search_result.GetChildren()
            highlight_who = re.sub(r'(\s+)', r'</em>\1<em>', who)
            for search_result_item in search_result_items:
                if (
                    search_result_item.ControlTypeName == 'PaneControl'
                    and search_result_item.TextControl(Name='聊天记录').Exists(0)
                ) or search_result_item.Name == f'搜索 {who}':
                    break
                elif (
                    search_result_item.ControlTypeName == 'ListItemControl'
                    and search_result_item.TextControl(Name=f"微信号: <em>{who}</em>").Exists(0)
                ):
                    search_result_item.Click()
                    return search_result_item.Name
                elif (
                    search_result_item.ControlTypeName == 'ListItemControl'
                    and search_result_item.TextControl(Name=f"昵称: <em>{highlight_who}</em>").Exists(0)
                ):
                    search_result_item.Click()
                    return search_result_item.Name
                elif (
                    search_result_item.ControlTypeName == 'ListItemControl'
                    and search_result_item.TextControl(Name=f"群聊名称: <em>{highlight_who}</em>").Exists(0)
                ):
                    search_result_item.Click()
                    return search_result_item.Name
                elif (
                    search_result_item.ControlTypeName == 'ListItemControl'
                    and who == search_result_item.Name
                ):
                    search_result_item.Click()
                    return who
                elif (
                    search_result_item.ControlTypeName == 'ListItemControl'
                    and who in search_result_item.Name
                ):
                    results.append(search_result_item)
            
        if exact:
            if search_result.Exists(0):
                search_result.SendKeys('{Esc}')
            return None
        if results:
            results[0].Click()
            return results[0].Name
        
        if search_result.Exists(0):
            search_result.SendKeys('{Esc}')
    
    def AtAll(self, msg=None, who=None):
        """@所有人
        
        Args:
            who (str, optional): 要发送给谁，如果为None，则发送到当前聊天页面。  *最好完整匹配，优先使用备注
            msg (str, optional): 要发送的文本消息
        """
        if FindWindow(name=who, classname='ChatWnd'):
            chat = ChatWnd(who, self, self.language)  # 更新为正确的参数顺序
            chat.AtAll(msg)
            return None
        
        self._show()
        if who:
            try:
                editbox = self.ChatBox.EditControl(searchDepth=10)
                if who in self.CurrentChat() and who in editbox.Name:
                    pass
                else:
                    self.ChatWith(who)
                    editbox = self.ChatBox.EditControl(Name=who, searchDepth=10)
            except:
                self.ChatWith(who)
                editbox = self.ChatBox.EditControl(Name=who, searchDepth=10)
        else:
            editbox = self.ChatBox.EditControl(searchDepth=10)
        editbox.SendKeys('@')
        atwnd = self.UiaAPI.PaneControl(ClassName='ChatContactMenu')
        if atwnd.Exists(maxSearchSeconds=0.1):
            atwnd.ListItemControl(Name='所有人').Click(simulateMove=False)
            if msg:
                if not msg.startswith('\n'):
                    msg = '\n' + msg
                self.SendMsg(msg, who=who, clear=False)
            else:
                editbox.SendKeys('{Enter}')

    def SendMsg(self, msg, who=None, clear=True, at=None, background=True):
        """发送文本消息

        Args:
            msg (str): 要发送的文本消息
            who (str): 要发送给谁，如果为None，则发送到当前聊天页面
            clear (bool, optional): 是否清除原本的内容
            at (str|list, optional): 要@的人，可以是一个人或多个人
            background (bool, optional): 是否在后台发送，默认True

        Returns:
            WxResponse: 包含发送状态的响应对象
        """
        if FindWindow(name=who, classname='ChatWnd'):
            chat = ChatWnd(who, self, self.language)  # 更新为正确的参数顺序
            return chat.SendMsg(msg, at=at, background=background)
            
        if not msg and not at:
            return WxResponse.failure("消息为空，发送失败")
        
        # 如果选择后台发送，则加入队列
        if background:
            with self._queue_lock:
                self._message_queue.append({
                    'msg': msg,
                    'who': who,
                    'clear': clear,
                    'at': at
                })
            return WxResponse.success('消息已加入发送队列')
        else:
            # 否则直接发送
            return self._send_message_internal(msg, who, clear, at)        

    def SendTypingText(self, msg, who=None, clear=True, interval_time=0, exact=False):
        if who and FindWindow(name=who, classname='ChatWnd'):
            chat = ChatWnd(who, self, self.language)  # 现在参数顺序是正确的
            return chat.SendTypingText(msg, clear=clear, interval_time=interval_time)
    
        if not msg:
            return WxResponse.failure("消息为空，发送失败")
        
        if WxParam.MOUSE_MOVE:
            self._show()
            
        if who:
            try:
                editbox = self.ChatBox.EditControl(searchDepth=10)
                if who in self.CurrentChat() and who in editbox.Name:
                    pass
                else:
                    self.ChatWith(who, exact=exact)
                    editbox = self.ChatBox.EditControl(searchDepth=10)
            except:
                self.ChatWith(who, exact=exact)
                editbox = self.ChatBox.EditControl(searchDepth=10)
        else:
            editbox = self.ChatBox.EditControl(searchDepth=10)

        if clear:
            editbox.ShortcutSelectAll(move=WxParam.MOUSE_MOVE)

        def _at(name):
            """处理@某人的功能"""
            for char in name.replace(' ', ''):
                editbox.SendKeys(char)
                if interval_time > 0:
                    time.sleep(interval_time)
                    
            atwnd = self.UiaAPI.PaneControl(ClassName='ChatContactMenu')
            if atwnd.Exists(maxSearchSeconds=0.1):
                atwnd.ListItemControl(Name=name.replace('@', '')).Click(move=WxParam.MOUSE_MOVE, simulateMove=False, return_pos=False)
            else:
                wxlog.debug(f"未找到联系人：{name}")

        # 处理消息中的@标记
        atlist = re.findall(r'{(@.*?)}', msg)
        parts = re.split(r'{(@.*?)}', msg)
        
        for i in range(len(parts)):
            # 发送普通文本
            if parts[i]:
                for char in parts[i]:
                    if char == '\n':
                        editbox.SendKeys('{Shift}{Enter}')
                    else:
                        editbox.SendKeys(char)
                    if interval_time > 0:
                        time.sleep(interval_time)
            
            # 处理@标记
            if i < len(atlist):
                _at(atlist[i])
        
        editbox.SendKeys(WxParam.SHORTCUT_SEND)
        return WxResponse.success("发送成功")


    def _send_message_internal(self, msg, who=None, clear=True, at=None):
        """内部消息发送方法"""
        try:
            # self._show()
            if who:
                try:
                    editbox = self.ChatBox.EditControl(searchDepth=10)
                    if who in self.CurrentChat() and who in editbox.Name:
                        pass
                    else:
                        if not self.ChatWith(who):
                            return WxResponse.failure(f"找不到聊天对象: {who}")
                        editbox = self.ChatBox.EditControl(Name=who, searchDepth=10)
                except:
                    if not self.ChatWith(who):
                        return WxResponse.failure(f"找不到聊天对象: {who}")
                    editbox = self.ChatBox.EditControl(Name=who, searchDepth=10)
            else:
                editbox = self.ChatBox.EditControl(searchDepth=10)
                
            if clear:
                editbox.SendKeys('{Ctrl}a', waitTime=0)
                
            if not editbox.HasKeyboardFocus:
                editbox.Click(simulateMove=False)

            if at:
                if isinstance(at, str):
                    at = [at]
                for i in at:
                    editbox.SendKeys('@'+i)
                    atwnd = self.UiaAPI.PaneControl(ClassName='ChatContactMenu')
                    if atwnd.Exists(maxSearchSeconds=0.1):
                        atwnd.SendKeys('{ENTER}')
                        if msg and not msg.startswith('\n'):
                            msg = '\n' + msg

            t0 = time.time()
            while True:
                if time.time() - t0 > 10:
                    return WxResponse.failure(f'发送消息超时 --> {who} - {msg}')
                SetClipboardText(msg)
                editbox.SendKeys('{Ctrl}v')
                if editbox.GetValuePattern().Value:
                    break
                    
            editbox.SendKeys('{Enter}')
            return WxResponse.success("发送成功")
        
        except Exception as e:
            wxlog.debug(f"消息发送异常: {str(e)}\n{traceback.format_exc()}")
            return WxResponse.failure(f"消息发送异常: {str(e)}")

        
    def SendFiles(self, filepath, who=None, exact=False, background=True):
        """向聊天窗口发送文件
        
        Args:
            filepath (str|list): 要发送的文件的绝对路径  
            who (str): 要发送给谁，如果为None，则发送到当前聊天页面
            exact (bool, optional): 搜索who好友时是否精确匹配，默认False
            background (bool, optional): 是否在后台发送，默认True
                
        Returns:
            WxResponse: 包含发送状态的响应对象
        """
        if FindWindow(name=who, classname='ChatWnd'):
            chat = ChatWnd(who, self, self.language)  # 更新为正确的参数顺序
            return chat.SendFiles(filepath, background=background)
        
        # 验证文件
        filelist = []
        if isinstance(filepath, str):
            if not os.path.exists(filepath):
                wxlog.debug(f'未找到文件：{filepath}，无法成功发送')
                return WxResponse.failure(f'未找到文件：{filepath}，无法成功发送')
            else:
                filelist.append(os.path.realpath(filepath))
        elif isinstance(filepath, (list, tuple, set)):
            for i in filepath:
                if os.path.exists(i):
                    filelist.append(i)
                else:
                    wxlog.debug(f'未找到文件：{i}')
        else:
            wxlog.debug(f'filepath参数格式错误：{type(filepath)}，应为str、list、tuple、set格式')
            return WxResponse.failure(f'filepath参数格式错误：{type(filepath)}，应为str、list、tuple、set格式')
        
        if not filelist:
            wxlog.debug('所有文件都无法成功发送')
            return WxResponse.failure('所有文件都无法成功发送')
        
        # 如果选择后台发送，则加入队列
        if background:
            with self._queue_lock:
                self._file_queue.append({
                    'filepath': filelist,
                    'who': who,
                    'exact': exact
                })
            return WxResponse.success('文件已加入发送队列')
        else:
            # 否则直接发送
            return self._send_file_internal(filelist, who, exact)

    def _send_file_internal(self, filepath, who=None, exact=False):
        """内部文件发送方法"""
        try:
            # self._show()
            if who:
                try:
                    if who in self.CurrentChat() and who in self.ChatBox.EditControl(searchDepth=10).Name:
                        pass
                    else:
                        if not self.ChatWith(who, exact=exact):
                            return WxResponse.failure(f"找不到聊天对象: {who}")
                except:
                    if not self.ChatWith(who, exact=exact):
                        return WxResponse.failure(f"找不到聊天对象: {who}")
                editbox = self.ChatBox.EditControl()
            else:
                editbox = self.ChatBox.EditControl()
                
            # 选中所有文本并清空输入框
            editbox.SendKeys('{Ctrl}a', waitTime=0)
            
            # 设置文件到剪贴板并粘贴
            t0 = time.time()
            while True:
                if time.time() - t0 > 10:
                    return WxResponse.failure(f'发送文件超时 --> {filepath}')
                SetClipboardFiles(filepath)
                time.sleep(0.2)
                editbox.SendKeys('{Ctrl}v')
                
                # 验证粘贴是否成功
                t1 = time.time()
                edit_value = None
                while time.time() - t1 < 5:
                    try:
                        edit_value = editbox.GetValuePattern().Value
                        break
                    except:
                        time.sleep(0.1)
                        
                if edit_value:
                    break
            
            # 发送文件
            t0 = time.time()
            while time.time() - t0 < 10:
                t1 = time.time()
                edit_value = None
                while time.time() - t1 < 5:
                    try:
                        edit_value = editbox.GetValuePattern().Value
                        break
                    except:
                        time.sleep(0.1)
                        
                if not edit_value:
                    break
                editbox.SendKeys('{Enter}')
                time.sleep(0.1)
                
            return WxResponse.success('文件发送成功')
        
        except Exception as e:
            wxlog.debug(f"文件发送异常: {str(e)}\n{traceback.format_exc()}")
            return WxResponse.failure(f"文件发送异常: {str(e)}")

            
    def GetAllMessage(self, savepic=False, savefile=False, savevoice=False):
        '''获取当前窗口中加载的所有聊天记录
        
        Args:
            savepic (bool): 是否自动保存聊天图片
            
        Returns:
            list: 聊天记录信息
        '''
        if not self.C_MsgList.Exists(0.2):
            return []
        MsgItems = self.C_MsgList.GetChildren()
        msgs = self._getmsgs(MsgItems, savepic, savefile=savefile, savevoice=savevoice)
        return msgs
    
    def LoadMoreMessage(self):
        """加载当前聊天页面更多聊天信息
        
        Returns:
            bool: 是否成功加载更多聊天信息
        """
        loadmore = self.C_MsgList.GetFirstChildControl()
        loadmore_top = loadmore.BoundingRectangle.top
        top = self.C_MsgList.BoundingRectangle.top
        while True:
            if loadmore.BoundingRectangle.top > top or loadmore.Name == '':
                isload = True
                break
            else:
                self.C_MsgList.WheelUp(wheelTimes=10, waitTime=0.1)
                if loadmore.BoundingRectangle.top == loadmore_top:
                    isload = False
                    break
                else:
                    loadmore_top = loadmore.BoundingRectangle.top
        self.C_MsgList.WheelUp(wheelTimes=1, waitTime=0.1)
        return isload
    
    def CurrentChat(self):
        '''获取当前聊天对象名'''
        uia.SetGlobalSearchTimeout(1)
        try:
            currentname = self.ChatBox.TextControl(searchDepth=15).Name
            return currentname
        except:
            return None
        finally:
            uia.SetGlobalSearchTimeout(10)

    def GetNewFriends(self):
        """获取新的好友申请列表
        
        Returns:
            list: 新的好友申请列表，元素为NewFriendsElement对象，可直接调用Accept方法

        Example:
            >>> wx = WeChat()
            >>> newfriends = wx.GetNewFriends()
            >>> tags = ['标签1', '标签2']
            >>> for friend in newfriends:
            ...     remark = f'备注{friend.name}'
            ...     friend.Accept(remark=remark, tags=tags)  # 接受好友请求，并设置备注和标签
        """
        self._show()
        self.SwitchToContact()
        self.SessionBox.ButtonControl(Name='ContactListItem').Click(simulateMove=False)
        NewFriendsList = [NewFriendsElement(i, self) for i in self.ChatBox.ListControl(Name='新的朋友').GetChildren()]
        AcceptableNewFriendsList = [i for i in NewFriendsList if i.acceptable]
        wxlog.debug(f'获取到 {len(AcceptableNewFriendsList)} 条新的好友申请')
        return AcceptableNewFriendsList
    
    def AddListenChat(self, who, savepic=False, savefile=False, savevoice=False):
        """添加监听对象
        
        Args:
            who (str|list): 要监听的聊天对象名，可以是字符串或列表
            savepic (bool, optional): 是否自动保存聊天图片，只针对该聊天对象有效
            savefile (bool, optional): 是否自动保存聊天文件，只针对该聊天对象有效
            savevoice (bool, optional): 是否自动保存聊天语音，只针对该聊天对象有效
        """
        # 支持列表形式的参数
        if isinstance(who, list):
            for i in who:
                self.AddListenChat(i, savepic, savefile, savevoice)
            return None
            
        exists = uia.WindowControl(searchDepth=1, ClassName='ChatWnd', Name=who).Exists(maxSearchSeconds=0.1)
        if not exists:
            who = self.ChatWith(who)
            self.SessionBox.ListItemControl(RegexName=who).DoubleClick(simulateMove=False)
        
        self.listen[who] = ChatWnd(who, self, self.language)  # 更新为正确的参数顺序
        self.listen[who].savepic = savepic
        self.listen[who].savefile = savefile
        self.listen[who].savevoice = savevoice
        
        return WxResponse.success(f"添加监听对象 {who} 成功")


    def GetListenMessage(self, who=None):
        """获取监听对象的新消息
        
        Args:
            who (str, optional): 要获取消息的聊天对象名，如果为None，则获取所有监听对象的消息

        Returns:
            dict: 包含聊天对象和消息的字典
        """
        # 检查指定的单个监听对象
        if who and who in self.listen:
            chat = self.listen.get(who)
            # 检查窗口是否存在
            if not chat.UiaAPI.Exists(0.1):
                wxlog.debug(f"监听对象`{who}`窗口不存在，尝试重新打开")
                # 尝试重新打开窗口
                if self.ChatWith(who):
                    # 重新创建ChatWnd对象
                    self.listen[who] = ChatWnd(who, self, self.language)
                    self.listen[who].savepic = chat.savepic
                    self.listen[who].savefile = chat.savefile
                    self.listen[who].savevoice = chat.savevoice
                    chat = self.listen[who]
                    wxlog.debug(f"已重新打开监听对象`{who}`窗口")
                else:
                    wxlog.debug(f"无法重新打开监听对象`{who}`窗口，删除监听")
                    del self.listen[who]
                    return {}
                    
            if chat in self._listener_messages:
                msgs = self._listener_messages.pop(chat)
                return {chat: msgs}
            else:
                return {}
        # 处理所有监听对象
        else:
            result = {}
            # 创建副本以避免在迭代时修改字典
            temp_listen = self.listen.copy()
            for chat_who, chat in list(temp_listen.items()):
                # 检查窗口是否存在
                if not chat.UiaAPI.Exists(0.1):
                    wxlog.debug(f"监听对象`{chat_who}`窗口不存在，尝试重新打开")
                    # 尝试重新打开窗口
                    if self.ChatWith(chat_who):
                        # 重新创建ChatWnd对象
                        self.listen[chat_who] = ChatWnd(chat_who, self, self.language)
                        self.listen[chat_who].savepic = chat.savepic
                        self.listen[chat_who].savefile = chat.savefile
                        self.listen[chat_who].savevoice = chat.savevoice
                        # 更新引用
                        chat = self.listen[chat_who]
                        wxlog.debug(f"已重新打开监听对象`{chat_who}`窗口")
                    else:
                        wxlog.debug(f"无法重新打开监听对象`{chat_who}`窗口，删除监听")
                        del self.listen[chat_who]
                        continue
                        
                # 获取该监听对象的消息
                if chat in self._listener_messages:
                    result[chat] = self._listener_messages.pop(chat)
                    
            return result

    
    # def GetListenMessage(self, who=None):
    #     """获取监听对象的新消息
        
    #     Args:
    #         who (str, optional): 要获取消息的聊天对象名，如果为None，则获取所有监听对象的消息

    #     Returns:
    #         str|dict: 如果
    #     """
    #     wxlog.debug('GetListenMessage获取监听对象的新消息')
    #     temp_listen = self.listen.copy()
    #     if who and who in temp_listen:
    #         chat = temp_listen.get(who, None)
    #         try:
    #             if chat is None or not chat.UiaAPI.Exists(0.1):
    #                 try:
    #                     wxlog.debug(f"`{who}`窗口不存在，删除监听")
    #                     del self.listen[who]
    #                 except:
    #                     pass
    #                 return {}
    #         except:
    #             exc = traceback.format_exc()
    #             wxlog.debug(f"获取监听对象的新消息异常：{exc}")
    #             return {}
    #         msg = chat.GetNewMessage(
    #             savepic=chat.savepic, 
    #             savefile=chat.savefile, 
    #             savevoice=chat.savevoice, 
    #             parseurl=chat.parseurl
    #         )
    #         wxlog.debug(f"`{who}`返回监听消息{msg}")
    #         return msg
    #     msgs = {}
    #     for who in temp_listen:
    #         chat = temp_listen.get(who, None)
    #         try:
    #             if chat is None or not chat.UiaAPI.Exists(0.1):
    #                 try:
    #                     wxlog.debug(f"`{who}`窗口不存在，删除监听")
    #                     del self.listen[who]
    #                 except:
    #                     pass
    #                 continue
    #         except:
    #             continue
    #         msg = chat.GetNewMessage(
    #             savepic=chat.savepic, 
    #             savefile=chat.savefile, 
    #             savevoice=chat.savevoice,
    #         )
    #         if msg:
    #             msgs[chat] = msg
    #     wxlog.debug(f"`{who}`返回监听消息{msg}")
    #     return msgs


    def SwitchToContact(self):
        """切换到通讯录页面"""
        self._show()
        self.A_ContactsIcon.Click(simulateMove=False)

    def SwitchToChat(self):
        """切换到聊天页面"""
        self._show()
        self.A_ChatIcon.Click(simulateMove=False)

    # def DownloadFiles(self, who, amount=1):
    #     """切换到聊天文件页面
        
    #     Args:
    #         who (str): 要下载文件的聊天对象名
    #         amount (int): 要下载的文件数量
    #     """
    #     self._show()
    #     self.A_FilesIcon.Click(simulateMove=False)
    #     files = WeChatFiles()
    #     files.ChatWithFile(who)
    #     files.DownloadFiles(who, amount)
    #     files.Close()

    def GetGroupMembers(self):
        """获取当前聊天群成员

        Returns:
            list: 当前聊天群成员列表
        """
        ele = self.ChatBox.PaneControl(searchDepth=7, foundIndex=6).ButtonControl(Name='聊天信息')
        try:
            uia.SetGlobalSearchTimeout(1)
            rect = ele.BoundingRectangle
            Click(rect)
        except:
            return 
        finally:
            uia.SetGlobalSearchTimeout(10)
        roominfoWnd = self.UiaAPI.Control(ClassName='SessionChatRoomDetailWnd', searchDepth=1)
        more = roominfoWnd.ButtonControl(Name='查看更多', searchDepth=8)
        try:
            uia.SetGlobalSearchTimeout(1)
            rect = more.BoundingRectangle
            Click(rect)
        except:
            pass
        finally:
            uia.SetGlobalSearchTimeout(10)
        members = [i.Name for i in roominfoWnd.ListControl(Name='聊天成员').GetChildren()]
        while members[-1] in ['添加', '移出']:
            members = members[:-1]
        roominfoWnd.SendKeys('{Esc}')
        return members

    def GetAllFriends(self, keywords=None):
        """获取所有好友列表
        注：
            1. 该方法运行时间取决于好友数量，约每秒6~8个好友的速度
            2. 该方法未经过大量测试，可能存在未知问题，如有问题请微信群内反馈
        
        Args:
            keywords (str, optional): 搜索关键词，只返回包含关键词的好友列表
            
        Returns:
            list: 所有好友列表
        """
        self._show()
        self.SwitchToContact()
        self.SessionBox.ListControl(Name="联系人").ButtonControl(Name="通讯录管理").Click(simulateMove=False)
        contactwnd = ContactWnd()
        if keywords:
            contactwnd.Search(keywords)
        friends = contactwnd.GetAllFriends()
        contactwnd.Close()
        self.SwitchToChat()
        return friends
    
    def GetAllListenChat(self):
        """获取所有监听对象"""
        return self.listen
    
    def RemoveListenChat(self, who):
        """移除监听对象"""
        if who in self.listen:
            del self.listen[who]
        else:
            Warnings.lightred(f'未找到监听对象：{who}', stacklevel=2)

    def AddNewFriend(self, keywords, addmsg=None, remark=None, tags=None):
        """添加新的好友

        Args:
            keywords (str): 搜索关键词，微信号、手机号、QQ号
            addmsg (str, optional): 添加好友的消息
            remark (str, optional): 备注名
            tags (list, optional): 标签列表

        Example:
            >>> wx = WeChat()
            >>> keywords = '13800000000'      # 微信号、手机号、QQ号
            >>> addmsg = '你好，我是xxxx'      # 添加好友的消息
            >>> remark = '备注名字'            # 备注名
            >>> tags = ['朋友', '同事']        # 标签列表
            >>> wx.AddNewFriend(keywords, addmsg=addmsg, remark=remark, tags=tags)
        """
        self._show()
        self.SwitchToContact()
        self.SessionBox.ButtonControl(Name='添加朋友').Click(simulateMove=False)
        edit = self.SessionBox.EditControl(Name='微信号/手机号')
        edit.Click(simulateMove=False)
        edit.SendKeys(keywords)
        self.SessionBox.TextControl(Name=f'搜索：{keywords}').Click(simulateMove=False)

        ContactProfileWnd = uia.PaneControl(ClassName='ContactProfileWnd')
        if ContactProfileWnd.Exists(maxSearchSeconds=2):
            # 点击添加到通讯录
            ContactProfileWnd.ButtonControl(Name='添加到通讯录').Click(simulateMove=False)
        else:
            wxlog.debug('未找到联系人')
            return False

        NewFriendsWnd = self.UiaAPI.WindowControl(ClassName='WeUIDialog')
        if NewFriendsWnd.Exists(maxSearchSeconds=2):
            if addmsg:
                msgedit = NewFriendsWnd.TextControl(Name="发送添加朋友申请").GetParentControl().EditControl()
                msgedit.Click(simulateMove=False)
                msgedit.SendKeys('{Ctrl}a', waitTime=0)
                msgedit.SendKeys(addmsg)

            if remark:
                remarkedit = NewFriendsWnd.TextControl(Name='备注名').GetParentControl().EditControl()
                remarkedit.Click(simulateMove=False)
                remarkedit.SendKeys('{Ctrl}a', waitTime=0)
                remarkedit.SendKeys(remark)

            if tags:
                tagedit = NewFriendsWnd.TextControl(Name='标签').GetParentControl().EditControl()
                for tag in tags:
                    tagedit.Click(simulateMove=False)
                    tagedit.SendKeys(tag)
                    NewFriendsWnd.PaneControl(ClassName='DropdownWindow').TextControl().Click(simulateMove=False)

            NewFriendsWnd.ButtonControl(Name='确定').Click(simulateMove=False)
        return True
    
    # 添加后台发送者方法
    def _background_sender(self):
        """后台消息和文件发送线程"""
        while not self._sender_stop_event.is_set():
            # 处理消息队列
            with self._queue_lock:
                if self._message_queue:
                    item = self._message_queue.pop(0)
                    self._send_message_internal(**item)
                
                # 处理文件队列
                if self._file_queue:
                    item = self._file_queue.pop(0)
                    self._send_file_internal(**item)
            
            # 防止CPU占用过高
            time.sleep(0.5)

    def _stop_sender(self):
        """停止发送者线程"""
        self._sender_stop_event.set()
        self._sender_thread.join()

class WeChatFiles:
    def __init__(self, language='cn') -> None:
        self.language = language
        self.api = uia.WindowControl(ClassName='FileListMgrWnd', searchDepth=1)
        MainControl3 = [i for i in self.api.GetChildren() if not i.ClassName][0]
        self.FileBox ,self.Search ,self.SessionBox = MainControl3.GetChildren()

        self.allfiles = self.SessionBox.ButtonControl(Name=self._lang('全部'))
        self.recentfiles = self.SessionBox.ButtonControl(Name=self._lang('最近使用'))
        self.whofiles = self.SessionBox.ButtonControl(Name=self._lang('发送者'))
        self.chatfiles = self.SessionBox.ButtonControl(Name=self._lang('聊天'))
        self.typefiles = self.SessionBox.ButtonControl(Name=self._lang('类型'))

    def GetSessionName(self, SessionItem):
        """获取聊天对象的名字

        Args:
            SessionItem (uiautomation.ListItemControl): 聊天对象控件

        Returns:
            sessionname (str): 聊天对象名
        """
        return SessionItem.Name

    def GetSessionList(self, reset=False, newmessage=False):
        """获取当前聊天列表中的所有聊天对象
        
        Args:
            reset (bool): 是否重置SessionItemList
            newmessage (bool): 是否只获取有新消息的聊天对象
            
        Returns:
            SessionList (dict): 聊天对象列表，键为聊天对象名，值为新消息条数
        """
        self.SessionItem = self.SessionBox.ListItemControl()
        if reset:
            self.SessionItemList = []
        
        SessionList = {}
        for i in range(100):  # 限制最大遍历次数避免无限循环
            if self.SessionItem.BoundingRectangle.width() != 0:
                try:
                    name, amount = self.GetSessionAmont(self.SessionItem)
                except:
                    break
                    
                if name not in self.SessionItemList:
                    self.SessionItemList.append(name)
                if name not in SessionList:
                    SessionList[name] = amount
                    
            self.SessionItem = self.SessionItem.GetNextSiblingControl()
            if not self.SessionItem:
                break
            
        if newmessage:
            return {i: SessionList[i] for i in SessionList if SessionList[i] > 0}
        return SessionList

    def __repr__(self) -> str:
        return f"<wxauto WeChat Image at {hex(id(self))}>"

    def _lang(self, text):
        return FILE_LANGUAGE[text][self.language]

    def _show(self):
        HWND = FindWindow(classname='ImagePreviewWnd')
        win32gui.ShowWindow(HWND, 1)
        self.api.SwitchToThisWindow()

    def ChatWithFile(self, who):
        '''打开某个聊天会话

        Args:
            who ( str ): 要打开的聊天框好友名。

        Returns:
            chatname ( str ): 打开的聊天框的名字。
        '''
        self._show()
        self.chatfiles.Click(simulateMove=False)
        sessiondict = self.GetSessionList(True)

        if who in sessiondict:
            # 直接点击已存在的聊天框
            self.SessionBox.ListItemControl(Name=who).Click(simulateMove=False)
            return who
        else:
            # 如果聊天框不在列表中，则抛出异常
            raise TargetNotFoundError(f'未查询到目标：{who}')

    def DownloadFiles(self, who, amount, deadline=None, size=None):
        '''开始下载文件

        Args:
            who ( str )：聊天名称
            amount ( num )：下载的文件数量限制。
            deadline ( str )：截止日期限制。
            size ( str )：文件大小限制。

        Returns:
            result ( bool )：下载是否成功

        '''
        self._show()
        itemlist = self.GetSessionList()
        if who in itemlist:
            self.item = self.SessionBox.ListItemControl(Name=who)
            self.item.Click(simulateMove=False)
        else:
            wxlog.debug(f'未查询到目标：{who}')
        itemfileslist = []

        item = self.SessionBox.ListControl(Name='', searchDepth=7).GetParentControl()
        item = item.GetNextSiblingControl()
        item = item.ListControl(Name='', searchDepth=5).GetChildren()
        del item[0]

        for i in range(amount):
            try:

                itemfileslist.append(item[i].Name)
                self.itemfiles = item[i]
                self.itemfiles.Click()
                time.sleep(0.5)
            except:
                pass

    def Close(self):
        self._show()
        self.api.SendKeys('{Esc}')


