# 许可证合规说明 / License Compliance Guide

## 项目许可证 / Project License

WeChatBot_WXAUTO_SE 遵循 **GNU GPL-3.0 或更高版本** 许可证。

## 依赖库许可证 / Dependencies License

### 核心依赖 (必需)
所有核心依赖库均使用与GPL-3.0兼容的许可证：
- **Waitress** (Zope Public License 2.1) - 生产级WSGI服务器
- Flask, SQLAlchemy, Requests, BeautifulSoup4 等使用 MIT/BSD/Apache 2.0 许可证

### 微信自动化库 (可选增强)
项目使用智能导入机制，按优先级尝试以下库：

1. **wxautox_wechatbot** (优先)
   - 私有许可证，专门授权给本项目使用
   - 提供增强功能和性能优化

2. **wxautox** (备选)
   - 私有许可证，用户需自行安装并取得作者授权
   - 提供与wxautox_wechatbot库一致的增强功能和性能优化

3. **wxauto** (开源备选)
   - Apache License 2.0
   - 完全开源，GPL-3.0兼容
   - 提供完整的基础功能

## 用户选择 / User Options

### 完全开源使用
如果您希望使用完全开源的版本：
1. **必须**安装 `waitress` (ZPL 2.1许可证)
2. 只安装 `wxauto` 库（微信自动化）
3. 不安装私有许可证库
4. 项目将自动使用开源实现
5. **所有组件均为GPL-3.0兼容的开源许可证**

### 增强功能使用
如果您有私有库的使用权限：
1. **必须**安装 `waitress` (ZPL 2.1许可证)
2. 运行 `Run.bat` 自动安装所有组件
3. 系统会自动选择最佳可用库
4. 享受增强的功能和性能

## GPL-3.0 合规性 / GPL-3.0 Compliance

1. **必需组件开源**：Waitress使用ZPL 2.1许可证，完全开源且GPL-3.0兼容
2. **可选依赖**：私有微信自动化库是可选增强，非必需组件
3. **开源备选**：微信自动化功能始终提供GPL兼容的开源实现（wxauto）
4. **用户自由**：用户可以选择完全开源的使用方式
5. **源代码提供**：所有GPL覆盖的代码均提供源代码
6. **透明声明**：清楚说明所有依赖和许可证情况
7. **完整开源路径**：通过 Waitress + wxauto 组合，可实现100%开源部署

## 分发说明 / Distribution Notes

- 分发本项目时，必须包含完整的源代码
- **必须**在 `requirements.txt` 中包含 Waitress 依赖
- 用户有权选择使用开源版本或增强版本（微信自动化库）
- 必须保留所有许可证文件和声明
- 任何修改都必须遵循GPL-3.0条款

## 法律声明 / Legal Notice

本项目的GPL-3.0许可证不受可选私有依赖的影响。用户在任何情况下都享有GPL-3.0赋予的完整自由软件权利。

### Waitress许可证声明
Waitress是本项目的必需组件，使用Zope Public License (ZPL) 2.1：
- 与GPL-3.0完全兼容
- 允许自由使用、修改和分发
- 无版权费用或使用限制
- 完整许可证：https://github.com/Pylons/waitress/blob/main/LICENSE.txt

---
更新日期：2025年10月  
维护者：iwyxdxl
