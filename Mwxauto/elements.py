# -*- coding: utf-8 -*-

from . import uiautomation as uia
from .languages import *
from .utils import *
from .color import *
from .errors import *
import datetime
import time
import os
import re
import win32gui
import win32con


class WxParam:
    SYS_TEXT_HEIGHT = 33
    TIME_TEXT_HEIGHT = 34
    RECALL_TEXT_HEIGHT = 45
    CHAT_TEXT_HEIGHT = 52
    CHAT_IMG_HEIGHT = 117
    DEFALUT_SAVEPATH = os.path.join(os.getcwd(), 'wxauto文件')
    SHORTCUT_SEND = '{Enter}'
    MOUSE_MOVE = False  # 关键参数：控制是否移动鼠标

class WeChatBase:
    def _lang(self, text, langtype='MAIN'):
        if langtype == 'MAIN':
            return MAIN_LANGUAGE[text][self.language]
        elif langtype == 'WARNING':
            return WARNING[text][self.language]

    def _split(self, MsgItem):
        uia.SetGlobalSearchTimeout(0)
        MsgItemName = MsgItem.Name
        if MsgItem.BoundingRectangle.height() == WxParam.SYS_TEXT_HEIGHT:
            Msg = ['SYS', MsgItemName, ''.join([str(i) for i in MsgItem.GetRuntimeId()])]
        elif MsgItem.BoundingRectangle.height() == WxParam.TIME_TEXT_HEIGHT:
            Msg = ['Time', MsgItemName, ''.join([str(i) for i in MsgItem.GetRuntimeId()])]
        elif MsgItem.BoundingRectangle.height() == WxParam.RECALL_TEXT_HEIGHT:
            if '撤回' in MsgItemName:
                Msg = ['Recall', MsgItemName, ''.join([str(i) for i in MsgItem.GetRuntimeId()])]
            else:
                Msg = ['SYS', MsgItemName, ''.join([str(i) for i in MsgItem.GetRuntimeId()])]
        else:
            Index = 1
            User = MsgItem.ButtonControl(foundIndex=Index)
            try:
                while True:
                    if User.Name == '':
                        Index += 1
                        User = MsgItem.ButtonControl(foundIndex=Index)
                    else:
                        break
                winrect = MsgItem.BoundingRectangle
                mid = (winrect.left + winrect.right)/2
                if User.BoundingRectangle.left < mid:
                    if MsgItem.TextControl().Exists(0.1) and MsgItem.TextControl().BoundingRectangle.top < User.BoundingRectangle.top:
                        name = (User.Name, MsgItem.TextControl().Name)
                    else:
                        name = (User.Name, User.Name)
                else:
                    name = 'Self'
                Msg = [name, MsgItemName, ''.join([str(i) for i in MsgItem.GetRuntimeId()])]
            except:
                Msg = ['SYS', MsgItemName, ''.join([str(i) for i in MsgItem.GetRuntimeId()])]
        uia.SetGlobalSearchTimeout(10.0)
        return ParseMessage(Msg, MsgItem, self)
    
    def _getmsgs(self, msgitems, savepic=False, savefile=False, savevoice=False):
        msgs = []
        for MsgItem in msgitems:
            if MsgItem.ControlTypeName == 'ListItemControl':
                msgs.append(self._split(MsgItem))

        msgtypes = [
            f"[{self._lang('图片')}]",
            f"[{self._lang('文件')}]",
            f"[{self._lang('语音')}]",
        ]

        if not [i for i in msgs if i.content[:4] in msgtypes]:
            return msgs

        for msg in msgs:
            if msg.type not in ('friend', 'self'):  # 排除非好友/自己类型的消息
                continue
                
            # 仅处理非自己发送的媒体消息（新增类型判断）
            if msg.content.startswith(f"[{self._lang('图片')}]") and savepic and msg.type != 'self':
                imgpath = self._download_pic(msg.control)
                msg.content = imgpath if imgpath else msg.content
                
            elif msg.content.startswith(f"[{self._lang('文件')}]") and savefile and msg.type != 'self':
                filepath = self._download_file(msg.control)
                msg.content = filepath if filepath else msg.content
                
            elif msg.content.startswith(f"[{self._lang('语音')}]") and savevoice and msg.type != 'self':
                voice_text = self._get_voice_text(msg.control)
                voice_text = f"[语音消息]{voice_text}"
                msg.content = voice_text if voice_text else msg.content
                
            msg.info[1] = msg.content
        return msgs
    
    def _download_pic(self, msgitem):
        
        savepath = os.path.join(WxParam.DEFALUT_SAVEPATH, f"微信图片_{datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')}.jpg")
        if savepath and os.path.exists(savepath):
            wxlog.debug(f"图片已存在: {savepath}, 跳过保存")
            return savepath

        self._show()
        imgcontrol = msgitem.ButtonControl(Name='')
        if not imgcontrol.Exists(0.5):
            return None
        RollIntoView(self.C_MsgList, imgcontrol)
        imgcontrol.Click(simulateMove=False)
        imgobj = WeChatImage()
        savepath = imgobj.Save()
        imgobj.Close()
        return savepath

    def _download_file(self, msgitem):
        # msgitems = self.C_MsgList.GetChildren()
        # msgs = []
        # for MsgItem in msgitems:
        #     msgs.append(self._split(MsgItem))
        
        filecontrol = msgitem.ButtonControl(Name='')
        if not filecontrol.Exists(0.5):
            return None
        RollIntoView(self.C_MsgList, filecontrol)
        filecontrol.RightClick(simulateMove=False)
        # paths = list()
        menu = self.UiaAPI.MenuControl(ClassName='CMenuWnd')
        options = [i for i in menu.ListControl().GetChildren() if i.ControlTypeName == 'MenuItemControl']

        copy = [i for i in options if i.Name == '复制']
        if copy:
            copy[0].Click(simulateMove=False)
        else:
            filecontrol.RightClick(simulateMove=False)
            filecontrol.Click(simulateMove=False)
            filewin = self.UiaAPI.WindowControl(ClassName='MsgFileWnd')
            accept_button = filewin.ButtonControl(Name='接收文件')
            if accept_button.Exists(2):
                accept_button.Click(simulateMove=False)
            
            while True:
                try:
                    filecontrol = msgitem.ButtonControl(Name='')
                    filecontrol.RightClick(simulateMove=False)
                    menu = self.UiaAPI.MenuControl(ClassName='CMenuWnd')
                    options = [i for i in menu.ListControl().GetChildren() if i.ControlTypeName == 'MenuItemControl']
                    copy = [i for i in options if i.Name == '复制']
                    if copy:
                        copy[0].Click(simulateMove=False)
                        break
                    else:
                        filecontrol.RightClick(simulateMove=False)
                except:
                    pass
        filepath = ReadClipboardData().get('15')[0]
        savepath = os.path.join(WxParam.DEFALUT_SAVEPATH, os.path.split(filepath)[1])
        if not os.path.exists(WxParam.DEFALUT_SAVEPATH):
            os.makedirs(WxParam.DEFALUT_SAVEPATH)
        shutil.copyfile(filepath, savepath)
        return savepath

    def _get_voice_text(self, msgitem):
        if msgitem.GetProgenyControl(8, 4):
            return msgitem.GetProgenyControl(8, 4).Name
        voicecontrol = msgitem.ButtonControl(Name='')
        if not voicecontrol.Exists(0.5):
            return None
        RollIntoView(self.C_MsgList, voicecontrol)
        msgitem.GetProgenyControl(7, 1).RightClick(simulateMove=False)
        menu = self.UiaAPI.MenuControl(ClassName='CMenuWnd')
        option = menu.MenuItemControl(Name="语音转文字")
        if not option.Exists(0.5):
            voicecontrol.Click(simulateMove=False)
            if not msgitem.GetProgenyControl(8, 4):
                return None
        else:
            option.Click(simulateMove=False)

        text = ''
        while True:
            if msgitem.GetProgenyControl(8, 4):
                if msgitem.GetProgenyControl(8, 4).Name == text:
                    return text
                text = msgitem.GetProgenyControl(8, 4).Name
            time.sleep(0.1)


class ChatWnd(WeChatBase):
    def __init__(self, who, wx=None, language='cn'):
        self.who = who
        self._wx = wx  # 存储WeChat实例引用
        self.language = language
        self.usedmsgid = []
        self.UiaAPI = uia.WindowControl(searchDepth=1, ClassName='ChatWnd', Name=who)
        self.editbox = self.UiaAPI.EditControl()
        self.C_MsgList = self.UiaAPI.ListControl()
        self.GetAllMessage()

        self.savepic = False   # 该参数用于在自动监听的情况下是否自动保存聊天图片
        self.savefile = False  # 是否保存文件
        self.savevoice = False  # 是否保存语音
        self.savevideo = False  # 是否保存视频
        self.parseurl = False  # 是否解析链接
    def __repr__(self) -> str:
        return f"<wxauto Chat Window at {hex(id(self))} for {self.who}>"

    def _show(self):
        self.HWND = FindWindow(name=self.who, classname='ChatWnd')
        win32gui.ShowWindow(self.HWND, 1)
        win32gui.SetWindowPos(self.HWND, -1, 0, 0, 0, 0, 3)
        win32gui.SetWindowPos(self.HWND, -2, 0, 0, 0, 0, 3)
        self.UiaAPI.SwitchToThisWindow()

    def AtAll(self, msg=None):
        """@所有人
        
        Args:
            msg (str, optional): 要发送的文本消息
        """
        wxlog.debug(f"@所有人：{self.who} --> {msg}")
        self._show()
        if not self.editbox.HasKeyboardFocus:
            self.editbox.Click(simulateMove=False)

        self.editbox.SendKeys('@')
        atwnd = self.UiaAPI.PaneControl(ClassName='ChatContactMenu')
        if atwnd.Exists(maxSearchSeconds=0.1):
            atwnd.ListItemControl(Name='所有人').Click(simulateMove=False)
            if msg:
                if not msg.startswith('\n'):
                    msg = '\n' + msg
                self.SendMsg(msg)
            else:
                self.editbox.SendKeys('{Enter}')

    def SendTypingText(self, msg, clear=True, interval_time=0):
        if not msg:
            return None
        
        self._show()
        if not self.editbox.HasKeyboardFocus:
            self.editbox.Click(simulateMove=False)

        if clear:
            self.editbox.SendKeys('{Ctrl}a', waitTime=0)

        def _at(name):
            """处理@某人的功能"""
            for char in name.replace(' ', ''):
                self.editbox.SendKeys(char)
                if interval_time > 0:
                    time.sleep(interval_time)
                    
            atwnd = self.UiaAPI.PaneControl(ClassName='ChatContactMenu')
            if atwnd.Exists(maxSearchSeconds=0.1):
                atwnd.ListItemControl(Name=name.replace('@', '')).Click(simulateMove=False)
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
                        self.editbox.SendKeys('{Shift}{Enter}')
                    else:
                        self.editbox.SendKeys(char)
                    if interval_time > 0:
                        time.sleep(interval_time)
            
            # 处理@标记
            if i < len(atlist):
                _at(atlist[i])
        
        self.editbox.SendKeys('{Enter}')
        return True


    def SendMsg(self, msg, at=None, background=True):
        """发送文本消息

        Args:
            msg (str): 要发送的文本消息
            at (str|list, optional): 要@的人，可以是一个人或多个人，格式为str或list，例如："张三"或["张三", "李四"]
            background (bool, optional): 是否在后台发送，默认True
        """
        wxlog.debug(f"发送消息：{self.who} --> {msg}")
        
        if not msg and not at:
            return False
        
        # 不再调用self._show()，这是后台操作的关键
        # 只需确保控件获取焦点，但不需要窗口前台化
        if not self.editbox.HasKeyboardFocus:
            self.editbox.Click(simulateMove=False)
        
        # 全选并清除当前文本
        self.editbox.ShortcutSelectAll(move=WxParam.MOUSE_MOVE)
        
        # 处理@功能
        if at:
            if isinstance(at, str):
                at = [at]
            for i in at:
                self.editbox.Input('@'+i)  # 使用Input而不是SendKeys
                atwnd = self.UiaAPI.PaneControl(ClassName='ChatContactMenu')
                if atwnd.Exists(maxSearchSeconds=0.1):
                    atwnd.SendKeys('{ENTER}')
                    if msg and not msg.startswith('\n'):
                        msg = '\n' + msg
                else:
                    # 处理@失败的情况
                    for _ in range(len(i)+1):
                        self.editbox.SendKeys('{BACK}')
        
        # 通过剪贴板粘贴文本
        t0 = time.time()
        while True:
            if time.time() - t0 > 10:
                wxlog.debug(f'发送消息超时 --> {self.who} - {msg}')
                return False
            
            SetClipboardText(msg)
            
            # 确保焦点，但不需要窗口前台化
            if not self.editbox.HasKeyboardFocus:
                self.editbox.Click(simulateMove=False)
                
            # 使用ShortcutPaste代替SendKeys('{Ctrl}v')
            self.editbox.ShortcutPaste(move=WxParam.MOUSE_MOVE)
            
            # 验证粘贴成功
            if self.editbox.GetValuePattern().Value.replace('￼', ''):
                break
            else:
                # 如果快捷键粘贴失败，尝试右键菜单粘贴
                wxlog.debug('快捷键粘贴失败，尝试右键粘贴')
                self.editbox.RightClick()
                menu = self.UiaAPI.MenuControl(ClassName='CMenuWnd')
                menu_item = menu.MenuItemControl(Name='粘贴')
                if menu_item.Exists(0.5):
                    menu_item.Click(simulateMove=False)
                    if self.editbox.GetValuePattern().Value.replace('￼', ''):
                        break
        
        # 发送消息
        t0 = time.time()
        while self.editbox.GetValuePattern().Value:
            if time.time() - t0 > 10:
                wxlog.debug(f'发送消息超时 --> {self.who} - {msg}')
                return False
                
            # 确保焦点，但不需要窗口前台化
            if not self.editbox.HasKeyboardFocus:
                self.editbox.Click(simulateMove=False)
                
            # 使用WxParam.SHORTCUT_SEND代替固定的{Enter}
            self.editbox.SendKeys(WxParam.SHORTCUT_SEND)
            time.sleep(0.2)
        
        return True

    def SendFiles(self, filepath, background=True):
        """向当前聊天窗口发送文件
        
        Args:
            filepath (str|list): 要复制文件的绝对路径
            background (bool, optional): 是否在后台发送，默认True
                
        Returns:
            bool: 是否成功发送文件
        """
        wxlog.debug(f"发送文件：{self.who} --> {filepath}")
        filelist = []
        if isinstance(filepath, str):
            if not os.path.exists(filepath):
                Warnings.lightred(f'未找到文件：{filepath}，无法成功发送', stacklevel=2)
                return False
            else:
                filelist.append(os.path.realpath(filepath))
        elif isinstance(filepath, (list, tuple, set)):
            for i in filepath:
                if os.path.exists(i):
                    filelist.append(i)
                else:
                    Warnings.lightred(f'未找到文件：{i}', stacklevel=2)
        else:
            Warnings.lightred(f'filepath参数格式错误：{type(filepath)}，应为str、list、tuple、set格式', stacklevel=2)
            return False
        
        if filelist:
            # 不再调用self._show()
            
            # 确保控件获取焦点并清空内容
            if not self.editbox.HasKeyboardFocus:
                self.editbox.Click(simulateMove=False)
                
            # 使用ShortcutSelectAll代替SendKeys('{Ctrl}a')
            self.editbox.ShortcutSelectAll(move=WxParam.MOUSE_MOVE)
            
            t0 = time.time()
            while True:
                if time.time() - t0 > 10:
                    Warnings.lightred(f'发送文件超时 --> {filelist}', stacklevel=2)
                    return False
                    
                SetClipboardFiles(filelist)
                time.sleep(0.2)
                
                # 使用ShortcutPaste代替SendKeys('{Ctrl}v')
                self.editbox.ShortcutPaste(move=WxParam.MOUSE_MOVE)
                
                # 检查粘贴是否成功
                t1 = time.time()
                edit_value = None
                while time.time() - t1 < 5:
                    try:
                        edit_value = self.editbox.GetValuePattern().Value
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
                        edit_value = self.editbox.GetValuePattern().Value
                        break
                    except:
                        time.sleep(0.1)
                        
                if not edit_value:
                    break
                    
                self.editbox.SendKeys(WxParam.SHORTCUT_SEND)
                time.sleep(0.1)
                
            return True
        else:
            Warnings.lightred('所有文件都无法成功发送', stacklevel=2)
            return False

    # # 添加优化版的UI自动化方法到现有类中
    # def ShortcutSelectAll(self, move=False):
    #     """全选文本的快捷方式"""
    #     self.SendKeys('{Ctrl}a', waitTime=0)
        
    # uia.Control.ShortcutSelectAll = ShortcutSelectAll

    # def ShortcutPaste(self, move=False):
    #     """粘贴的快捷方式"""
    #     self.SendKeys('{Ctrl}v', waitTime=0)
        
    # uia.Control.ShortcutPaste = ShortcutPaste

    # def Input(self, text):
    #     """优化的文本输入方法"""
    #     self.SendKeys(text, waitTime=0)
        
    # uia.Control.Input = Input

    def GetAllMessage(self, savepic=False, savefile=False, savevoice=False):
        '''获取当前窗口中加载的所有聊天记录
        
        Args:
            savepic (bool): 是否自动保存聊天图片
            savefile (bool): 是否自动保存聊天文件
            savevoice (bool): 是否自动保存语音转文字
            
        Returns:
            list: 聊天记录信息
        '''
        wxlog.debug(f"获取所有聊天记录：{self.who}")
        MsgItems = self.C_MsgList.GetChildren()
        msgs = self._getmsgs(MsgItems, savepic, savefile, savevoice)
        return msgs
    
    def GetNewMessage(self, savepic=False, savefile=False, savevoice=False):
        '''获取当前窗口中加载的新聊天记录

        Args:
            savepic (bool): 是否自动保存聊天图片
            savefile (bool): 是否自动保存聊天文件
            savevoice (bool): 是否自动保存聊天语音
        
        Returns:
            list: 新聊天记录信息
        '''
        # 获取当前消息ID列表
        now_msgid = [''.join([str(i) for i in i.GetRuntimeId()]) for i in self.C_MsgList.GetChildren()]
        
        # 初始化已使用的消息ID列表
        if not self.usedmsgid:
            wxlog.debug(f"`{self.who}`首次监听，缓存消息id")
            self.usedmsgid = now_msgid
            return []
        
        # 检查是否有新消息
        if not now_msgid or (now_msgid and now_msgid[-1] == self.usedmsgid[-1]):
            return []
        
        # 找出新消息
        MsgItems = self.C_MsgList.GetChildren()
        
        # 使用集合快速查找新消息
        old_msg_set = set(self.usedmsgid)
        new_msgs = []
        last_old_index = -1
        
        # 找出最后一个旧消息的位置
        for i, msg_id in enumerate(now_msgid):
            if msg_id in old_msg_set:
                last_old_index = i
        
        # 获取新消息（在最后一条旧消息之后的所有消息）
        if last_old_index != -1:
            NewMsgItems = MsgItems[last_old_index + 1:]
        else:
            # 如果没有找到旧消息，可能是所有消息都是新的
            NewMsgItems = MsgItems
        
        # 过滤掉已经处理过的消息
        NewMsgItems = [item for item in NewMsgItems if 
                    ''.join([str(i) for i in item.GetRuntimeId()]) not in old_msg_set]
        
        if not NewMsgItems:
            return []
        
        # 处理新消息
        newmsgs = self._getmsgs(NewMsgItems, savepic, savefile, savevoice)
        
        # 更新已使用的消息ID列表，保留最新的100条以防止列表过长
        self.usedmsgid = now_msgid[-100:]
        
        return newmsgs

    
    def LoadMoreMessage(self):
        """加载当前聊天页面更多聊天信息
        
        Returns:
            bool: 是否成功加载更多聊天信息
        """
        wxlog.debug(f"加载更多聊天信息：{self.who}")
        self._show()
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

    def GetGroupMembers(self):
        """获取当前聊天群成员

        Returns:
            list: 当前聊天群成员列表
        """
        wxlog.debug(f"获取当前聊天群成员：{self.who}")
        ele = self.UiaAPI.PaneControl(searchDepth=7, foundIndex=6).ButtonControl(Name='聊天信息')
        try:
            uia.SetGlobalSearchTimeout(1)
            rect = ele.BoundingRectangle
            Click(rect)
        except:
            return 
        finally:
            uia.SetGlobalSearchTimeout(10)
        roominfoWnd = self.UiaAPI.WindowControl(ClassName='SessionChatRoomDetailWnd', searchDepth=1)
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

class WeChatImage:
    def __init__(self, language='cn') -> None:
        self.language = language
        self.api = uia.WindowControl(ClassName='ImagePreviewWnd', searchDepth=1)
        MainControl1 = [i for i in self.api.GetChildren() if not i.ClassName][0]
        self.ToolsBox, self.PhotoBox = MainControl1.GetChildren()
        
        # tools按钮
        self.t_previous = self.ToolsBox.ButtonControl(Name=self._lang('上一张'))
        self.t_next = self.ToolsBox.ButtonControl(Name=self._lang('下一张'))
        self.t_zoom = self.ToolsBox.ButtonControl(Name=self._lang('放大'))
        self.t_translate = self.ToolsBox.ButtonControl(Name=self._lang('翻译'))
        self.t_ocr = self.ToolsBox.ButtonControl(Name=self._lang('提取文字'))
        self.t_save = self.ToolsBox.ButtonControl(Name=self._lang('另存为...'))
        self.t_qrcode = self.ToolsBox.ButtonControl(Name=self._lang('识别图中二维码'))

    def __repr__(self) -> str:
        return f"<wxauto WeChat Image at {hex(id(self))}>"
    
    def _lang(self, text):
        return IMAGE_LANGUAGE[text][self.language]
    
    def _show(self):
        HWND = FindWindow(classname='ImagePreviewWnd')
        win32gui.ShowWindow(HWND, 1)
        self.api.SwitchToThisWindow()
        
    def OCR(self):
        result = ''
        ctrls = self.PhotoBox.GetChildren()
        if len(ctrls) == 2:
            self.t_ocr.Click(simulateMove=False)
        ctrls = self.PhotoBox.GetChildren()
        if len(ctrls) != 3:
            Warnings.lightred('获取文字识别失败', stacklevel=2)
        else:
            TranslateControl = ctrls[-1]
            result = TranslateControl.TextControl().Name
        return result

    
    def Save(self, savepath='', timeout=10):
        """保存图片

        Args:
            savepath (str): 绝对路径，包括文件名和后缀，例如："D:/Images/微信图片_xxxxxx.jpg"
            （如果不填，则默认为当前脚本文件夹下，新建一个“微信图片”的文件夹，保存在该文件夹内）
        
        Returns:
            str: 文件保存路径，即savepath
        """
        
        if not savepath:
            savepath = os.path.join(WxParam.DEFALUT_SAVEPATH, f"微信图片_{datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')}.jpg")
        if not os.path.exists(os.path.split(savepath)[0]):
            os.makedirs(os.path.split(savepath)[0])

        # 如果文件已经存在，跳过保存过程
        if savepath and os.path.exists(savepath):
            wxlog.debug(f"图片已存在: {savepath}, 跳过保存")
            return savepath
            
        if self.t_zoom.Exists(maxSearchSeconds=5):
            self.t_save.Click(simulateMove=False)
        else:
            raise TimeoutError('下载超时')
        t0 = time.time()
        while True:
            if time.time() - t0 > timeout:
                raise TimeoutError('下载超时')
            handle = FindWindow(name='另存为...')
            if handle:
                break
        t0 = time.time()
        while True:
            if time.time() - t0 > timeout:
                raise TimeoutError('下载超时')
            try:
                edithandle = [i for i in GetAllWindowExs(handle) if i[1] == 'Edit' and i[-1]][0][0]
                savehandle = FindWinEx(handle, classname='Button', name='保存(&S)')[0]
                if edithandle and savehandle:
                    break
            except:
                pass
        win32gui.SendMessage(edithandle, win32con.WM_SETTEXT, '', str(savepath))
        win32gui.SendMessage(savehandle, win32con.BM_CLICK, 0, 0)
        return savepath
        
    def Previous(self):
        """上一张"""
        if self.t_previous.IsKeyboardFocusable:
            self._show()
            self.t_previous.Click(simulateMove=False)
            return True
        else:
            Warnings.lightred('上一张按钮不可用', stacklevel=2)
            return False
        
    def Next(self, warning=True):
        """下一张"""
        if self.t_next.IsKeyboardFocusable:
            self._show()
            self.t_next.Click(simulateMove=False)
            return True
        else:
            if warning:
                Warnings.lightred('已经是最新的图片了', stacklevel=2)
            return False
        
    def Close(self):
        self._show()
        self.api.SendKeys('{Esc}')
    
class TextElement:
    def __init__(self, ele, wx) -> None:
        self._wx = wx
        chatname = wx.CurrentChat()
        self.ele = ele
        self.sender = ele.ButtonControl(foundIndex=1, searchDepth=2)
        _ = ele.GetFirstChildControl().GetChildren()[1].GetChildren()
        if len(_) == 1:
            self.content = _[0].TextControl().Name
            self.chattype = 'friend'
            self.chatname = chatname
        else:
            self.sender_remark = _[0].TextControl().Name
            self.content = _[1].TextControl().Name
            self.chattype = 'group'
            numtext = re.findall(' \(\d+\)', chatname)[-1]
            self.chatname = chatname[:-len(numtext)]
            
        self.info = {
            'sender': self.sender.Name,
            'content': self.content,
            'chatname': self.chatname,
            'chattype': self.chattype,
            'sender_remark': self.sender_remark if hasattr(self, 'sender_remark') else ''
        }

    def __repr__(self) -> str:
        return f"<wxauto Text Element at {hex(id(self))} ({self.sender.Name}: {self.content})>"

class NewFriendsElement:
    def __init__(self, ele, wx):
        self._wx = wx
        self.ele = ele
        self.name = self.ele.Name
        self.msg = self.ele.GetFirstChildControl().PaneControl(SearchDepth=1).GetChildren()[-1].TextControl().Name
        self.ele.GetChildren()[-1]
        self.Status = self.ele.GetFirstChildControl().GetChildren()[-1]
        self.acceptable = False
        if isinstance(self.Status, uia.ButtonControl):
            self.acceptable = True

    def __repr__(self) -> str:
        return f"<wxauto New Friends Element at {hex(id(self))} ({self.name}: {self.msg})>"

    def Accept(self, remark=None, tags=None):
        """接受好友请求
        
        Args:
            remark (str, optional): 备注名
            tags (list, optional): 标签列表
        """
        wxlog.debug(f"接受好友请求：{self.name}  备注：{remark} 标签：{tags}")
        self._wx._show()
        self.Status.Click(simulateMove=False)
        NewFriendsWnd = self._wx.UiaAPI.WindowControl(ClassName='WeUIDialog')

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


class ContactWnd:
    def __init__(self):
        self.UiaAPI = uia.WindowControl(ClassName='ContactManagerWindow', searchDepth=1)
        self.Sidebar, _, self.ContactBox = self.UiaAPI.PaneControl(ClassName='', searchDepth=3, foundIndex=3).GetChildren()

    def __repr__(self) -> str:
        return f"<wxauto Contact Window at {hex(id(self))}>"

    def _show(self):
        self.HWND = FindWindow(classname='ContactManagerWindow')
        win32gui.ShowWindow(self.HWND, 1)
        win32gui.SetWindowPos(self.HWND, -1, 0, 0, 0, 0, 3)
        win32gui.SetWindowPos(self.HWND, -2, 0, 0, 0, 0, 3)
        self.UiaAPI.SwitchToThisWindow()

    def GetFriendNum(self):
        """获取好友人数"""
        wxlog.debug('获取好友人数')
        numText = self.Sidebar.PaneControl(Name='全部').TextControl(foundIndex=2).Name
        return int(re.findall('\d+', numText)[0])
    
    def Search(self, keyword):
        """搜索好友

        Args:
            keyword (str): 搜索关键词
        """
        wxlog.debug(f"搜索好友：{keyword}")
        self.ContactBox.EditControl(Name="搜索").Click(simulateMove=False)
        self.ContactBox.SendKeys('{Ctrl}{A}')
        self.ContactBox.SendKeys(keyword)

    def GetAllFriends(self):
        """获取好友列表"""
        wxlog.debug("获取好友列表")
        self._show()
        contacts_list = []
        while True:
            contact_ele_list = self.ContactBox.ListControl().GetChildren()
            for ele in contact_ele_list:
                contacts_info = {
                    'nickname': ele.TextControl().Name,
                    'remark': ele.ButtonControl(foundIndex=2).Name,
                    'tags': ele.ButtonControl(foundIndex=3).Name.split('，'),
                }
                if contacts_info.get('remark') in ('添加备注', ''):
                    contacts_info['remark'] = None
                if contacts_info.get('tags') in (['添加标签'], ['']):
                    contacts_info['tags'] = None
                if contacts_info not in contacts_list:
                    contacts_list.append(contacts_info)
            bottom = self.ContactBox.ListControl().GetChildren()[-1].BoundingRectangle.top
            self.ContactBox.WheelDown(wheelTimes=5, waitTime=0.1)
            if bottom == self.ContactBox.ListControl().GetChildren()[-1].BoundingRectangle.top:
                return contacts_list
    
    def Close(self):
        """关闭联系人窗口"""
        wxlog.debug('关闭联系人窗口')
        self._show()
        self.UiaAPI.SendKeys('{Esc}')


class ContactElement:
    def __init__(self, ele):
        self.element = ele
        self.nickname = ele.TextControl().Name
        self.remark = ele.ButtonControl(foundIndex=2).Name
        self.tags = ele.ButtonControl(foundIndex=3).Name.split('，')

    def __repr__(self) -> str:
        return f"<wxauto Contact Element at {hex(id(self))} ({self.nickname}: {self.remark})>"
    
    def EditRemark(self, remark: str):
        """修改好友备注名
        
        Args:
            remark (str): 新备注名
        """
        wxlog.debug(f"修改好友备注名：{self.nickname} --> {remark}")
        self.element.ButtonControl(foundIndex=2).Click(simulateMove=False)
        self.element.SendKeys('{Ctrl}a')
        self.element.SendKeys(remark)
        self.element.SendKeys('{Enter}')
        

class SessionElement:
    def __init__(self, item):
        self.name = item.GetProgenyControl(4, control_type='TextControl').Name\
            if item.GetProgenyControl(4, control_type='TextControl') else None
        self.time = item.GetProgenyControl(4, 1, control_type='TextControl').Name\
            if item.GetProgenyControl(4, 1, control_type='TextControl') else None
        self.content = item.GetProgenyControl(4, 2, control_type='TextControl').Name\
            if item.GetProgenyControl(4, 2, control_type='TextControl') else None
        self.isnew = item.GetProgenyControl(2, 2) is not None
        wxlog.debug(f"============== 【{self.name}】 ==============")
        wxlog.debug(f"最后一条消息时间: {self.time}")
        wxlog.debug(f"最后一条消息内容: {self.content}")
        wxlog.debug(f"是否有新消息: {self.isnew}")


class Message:
    type = 'message'

    def __getitem__(self, index):
        return self.info[index]
    
    def __str__(self):
        return self.content
    
    def __repr__(self):
        return str(self.info[:2])
    

class SysMessage(Message):
    type = 'sys'
    
    def __init__(self, info, control, wx):
        self.info = info
        self.control = control
        self.wx = wx
        self.sender = info[0]
        self.content = info[1]
        self.id = info[-1]
        wxlog.debug(f"【系统消息】{self.content}")
    
    # def __repr__(self):
    #     return f'<wxauto SysMessage at {hex(id(self))}>'
    

class TimeMessage(Message):
    type = 'time'
    
    def __init__(self, info, control, wx):
        self.info = info
        self.control = control
        self.wx = wx
        self.time = ParseWeChatTime(info[1])
        self.sender = info[0]
        self.content = info[1]
        self.id = info[-1]
        wxlog.debug(f"【时间消息】{self.time}")
    
    # def __repr__(self):
    #     return f'<wxauto TimeMessage at {hex(id(self))}>'
    

class RecallMessage(Message):
    type = 'recall'
    
    def __init__(self, info, control, wx):
        self.info = info
        self.control = control
        self.wx = wx
        self.sender = info[0]
        self.content = info[1]
        self.id = info[-1]
        wxlog.debug(f"【撤回消息】{self.content}")
    
    # def __repr__(self):
    #     return f'<wxauto RecallMessage at {hex(id(self))}>'
    

class SelfMessage(Message):
    type = 'self'
    
    def __init__(self, info, control, obj):
        self.info = info
        self.control = control
        self._winobj = obj
        self.sender = info[0]
        self.content = info[1]
        self.id = info[-1]
        self.chatbox = obj.ChatBox if hasattr(obj, 'ChatBox') else obj.UiaAPI
        wxlog.debug(f"【自己消息】{self.content}")
    
    # def __repr__(self):
    #     return f'<wxauto SelfMessage at {hex(id(self))}>'

    def quote(self, msg):
        """引用该消息

        Args:
            msg (str): 引用的消息内容

        Returns:
            bool: 是否成功引用
        """
        wxlog.debug(f'发送引用消息：{msg}  --> {self.sender} | {self.content}')
        self._winobj._show()
        headcontrol = [i for i in self.control.GetFirstChildControl().GetChildren() if i.ControlTypeName == 'ButtonControl'][0]
        RollIntoView(self.chatbox.ListControl(), headcontrol, equal=True)
        xbias = int(headcontrol.BoundingRectangle.width()*1.5)
        headcontrol.RightClick(x=-xbias, simulateMove=False)
        menu = self._winobj.UiaAPI.MenuControl(ClassName='CMenuWnd')
        quote_option = menu.MenuItemControl(Name="引用")
        if not quote_option.Exists(maxSearchSeconds=0.1):
            wxlog.debug('该消息当前状态无法引用')
            return False
        quote_option.Click(simulateMove=False)
        editbox = self.chatbox.EditControl(searchDepth=15)
        t0 = time.time()
        while True:
            if time.time() - t0 > 10:
                raise TimeoutError(f'发送消息超时 --> {msg}')
            SetClipboardText(msg)
            editbox.SendKeys('{Ctrl}v')
            if editbox.GetValuePattern().Value.replace('\r￼', ''):
                break
        editbox.SendKeys('{Enter}')
        return True
    
    def forward(self, friend):
        """转发该消息
        
        Args:
            friend (str): 转发给的好友昵称、备注或微信号
        
        Returns:
            bool: 是否成功转发
        """
        wxlog.debug(f'转发消息：{self.sender} --> {friend} | {self.content}')
        self._winobj._show()
        headcontrol = [i for i in self.control.GetFirstChildControl().GetChildren() if i.ControlTypeName == 'ButtonControl'][0]
        RollIntoView(self.chatbox.ListControl(), headcontrol, equal=True)
        xbias = int(headcontrol.BoundingRectangle.width()*1.5)
        headcontrol.RightClick(x=-xbias, simulateMove=False)
        menu = self._winobj.UiaAPI.MenuControl(ClassName='CMenuWnd')
        forward_option = menu.MenuItemControl(Name="转发...")
        if not forward_option.Exists(maxSearchSeconds=0.1):
            wxlog.debug('该消息当前状态无法转发')
            return False
        forward_option.Click(simulateMove=False)
        SetClipboardText(friend)
        contactwnd = self._winobj.UiaAPI.WindowControl(ClassName='SelectContactWnd')
        contactwnd.SendKeys('{Ctrl}a', waitTime=0)
        contactwnd.SendKeys('{Ctrl}v')
        checkbox = contactwnd.ListControl().CheckBoxControl()
        if checkbox.Exists(1):
            checkbox.Click(simulateMove=False)
            contactwnd.ButtonControl(Name='发送').Click(simulateMove=False)
            return True
        else:
            contactwnd.SendKeys('{Esc}')
            raise FriendNotFoundError(f'未找到好友：{friend}')
    
    def parse(self):
        """解析合并消息内容，当且仅当消息内容为合并转发的消息时有效"""
        wxlog.debug(f'解析合并消息内容：{self.sender} | {self.content}')
        self._winobj._show()
        headcontrol = [i for i in self.control.GetFirstChildControl().GetChildren() if i.ControlTypeName == 'ButtonControl'][0]
        RollIntoView(self.chatbox.ListControl(), headcontrol, equal=True)
        xbias = int(headcontrol.BoundingRectangle.width()*1.5)
        headcontrol.Click(x=-xbias, simulateMove=False)
        chatrecordwnd = uia.WindowControl(ClassName='ChatRecordWnd', searchDepth=1)
        msgitems = chatrecordwnd.ListControl().GetChildren()
        msgs = []
        for msgitem in msgitems:
            textcontrols = [i for i in GetAllControl(msgitem) if i.ControlTypeName == 'TextControl']
            who = textcontrols[0].Name
            time = textcontrols[1].Name
            try:
                content = textcontrols[2].Name
            except IndexError:
                content = ''
            msgs.append(([who, content, ParseWeChatTime(time)]))
        chatrecordwnd.SendKeys('{Esc}')
        return msgs

class FriendMessage(Message):
    type = 'friend'
    
    def __init__(self, info, control, obj):
        self.info = info
        self.control = control
        self._winobj = obj
        self.sender = info[0][0]
        self.sender_remark = info[0][1]
        self.content = info[1]
        self.id = info[-1]
        self.info[0] = info[0][0]
        self.chatbox = obj.ChatBox if hasattr(obj, 'ChatBox') else obj.UiaAPI
        if self.sender == self.sender_remark:
            wxlog.debug(f"【好友消息】{self.sender}: {self.content}")
        else:
            wxlog.debug(f"【好友消息】{self.sender}({self.sender_remark}): {self.content}")
    
    # def __repr__(self):
    #     return f'<wxauto FriendMessage at {hex(id(self))}>'

    def quote(self, msg):
        """引用该消息

        Args:
            msg (str): 引用的消息内容

        Returns:
            bool: 是否成功引用
        """
        wxlog.debug(f'发送引用消息：{msg}  --> {self.sender} | {self.content}')
        self._winobj._show()
        headcontrol = [i for i in self.control.GetFirstChildControl().GetChildren() if i.ControlTypeName == 'ButtonControl'][0]
        RollIntoView(self.chatbox.ListControl(), headcontrol, equal=True)
        xbias = int(headcontrol.BoundingRectangle.width()*1.5)
        headcontrol.RightClick(x=xbias, simulateMove=False)
        menu = self._winobj.UiaAPI.MenuControl(ClassName='CMenuWnd')
        quote_option = menu.MenuItemControl(Name="引用")
        if not quote_option.Exists(maxSearchSeconds=0.1):
            wxlog.debug('该消息当前状态无法引用')
            return False
        quote_option.Click(simulateMove=False)
        editbox = self.chatbox.EditControl(searchDepth=15)
        t0 = time.time()
        while True:
            if time.time() - t0 > 10:
                raise TimeoutError(f'发送消息超时 --> {msg}')
            SetClipboardText(msg)
            editbox.SendKeys('{Ctrl}v')
            if editbox.GetValuePattern().Value.replace('\r￼', ''):
                break
        editbox.SendKeys('{Enter}')
        return True
    
    def forward(self, friend):
        """转发该消息
        
        Args:
            friend (str): 转发给的好友昵称、备注或微信号
        
        Returns:
            bool: 是否成功转发
        """
        wxlog.debug(f'转发消息：{self.sender} --> {friend} | {self.content}')
        self._winobj._show()
        headcontrol = [i for i in self.control.GetFirstChildControl().GetChildren() if i.ControlTypeName == 'ButtonControl'][0]
        RollIntoView(self.chatbox.ListControl(), headcontrol, equal=True)
        xbias = int(headcontrol.BoundingRectangle.width()*1.5)
        headcontrol.RightClick(x=xbias, simulateMove=False)
        menu = self._winobj.UiaAPI.MenuControl(ClassName='CMenuWnd')
        forward_option = menu.MenuItemControl(Name="转发...")
        if not forward_option.Exists(maxSearchSeconds=0.1):
            wxlog.debug('该消息当前状态无法转发')
            return False
        forward_option.Click(simulateMove=False)
        SetClipboardText(friend)
        contactwnd = self._winobj.UiaAPI.WindowControl(ClassName='SelectContactWnd')
        contactwnd.SendKeys('{Ctrl}a', waitTime=0)
        contactwnd.SendKeys('{Ctrl}v')
        checkbox = contactwnd.ListControl().CheckBoxControl()
        if checkbox.Exists(1):
            checkbox.Click(simulateMove=False)
            contactwnd.ButtonControl(Name='发送').Click(simulateMove=False)
            return True
        else:
            contactwnd.SendKeys('{Esc}')
            raise FriendNotFoundError(f'未找到好友：{friend}')
    
    def parse(self):
        """解析合并消息内容，当且仅当消息内容为合并转发的消息时有效"""
        wxlog.debug(f'解析合并消息内容：{self.sender} | {self.content}')
        self._winobj._show()
        headcontrol = [i for i in self.control.GetFirstChildControl().GetChildren() if i.ControlTypeName == 'ButtonControl'][0]
        RollIntoView(self.chatbox.ListControl(), headcontrol, equal=True)
        xbias = int(headcontrol.BoundingRectangle.width()*1.5)
        headcontrol.Click(x=xbias, simulateMove=False)
        chatrecordwnd = uia.WindowControl(ClassName='ChatRecordWnd', searchDepth=1)
        msgitems = chatrecordwnd.ListControl().GetChildren()
        msgs = []
        for msgitem in msgitems:
            textcontrols = [i for i in GetAllControl(msgitem) if i.ControlTypeName == 'TextControl']
            who = textcontrols[0].Name
            time = textcontrols[1].Name
            try:
                content = textcontrols[2].Name
            except IndexError:
                content = ''
            msgs.append(([who, content, ParseWeChatTime(time)]))
        chatrecordwnd.SendKeys('{Esc}')
        return msgs



message_types = {
    'SYS': SysMessage,
    'Time': TimeMessage,
    'Recall': RecallMessage,
    'Self': SelfMessage
}

def ParseMessage(data, control, wx):
    return message_types.get(data[0], FriendMessage)(data, control, wx)


class LoginWnd:
    _class_name = 'WeChatLoginWndForPC'
    UiaAPI = uia.PaneControl(ClassName=_class_name, searchDepth=1)

    def __repr__(self) -> str:
        return f"<wxauto LoginWnd Object at {hex(id(self))}>"

    def _show(self):
        self.HWND = FindWindow(classname=self._class_name)
        win32gui.ShowWindow(self.HWND, 1)
        win32gui.SetWindowPos(self.HWND, -1, 0, 0, 0, 0, 3)
        win32gui.SetWindowPos(self.HWND, -2, 0, 0, 0, 0, 3)
        self.UiaAPI.SwitchToThisWindow()

    @property
    def _app_path(self):
        HWND = FindWindow(classname=self._class_name)
        return GetPathByHwnd(HWND)

    def login(self):
        enter_button = self.UiaAPI.ButtonControl(Name='进入微信')
        if enter_button.Exists():
            enter_button.Click(simulateMove=False)

    def get_qrcode(self):
        """获取登录二维码
        
        Returns:
            str: 二维码图片的保存路径
        """
        switch_account_button = self.UiaAPI.ButtonControl(Name='切换账号')
        if switch_account_button.Exists():
            switch_account_button.Click(simulateMove=False)
        self._show()
        qrcode_control = self.UiaAPI.ButtonControl(Name='二维码')
        qrcode = qrcode_control.ScreenShot()
        return qrcode
    