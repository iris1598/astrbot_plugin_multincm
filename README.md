# astrbot_plugin_multincm

AstrBot 网易云多选点歌插件，移植自 [nonebot-plugin-multincm]。

## ✨ 功能特性

- 🎵 **多类型搜索**：支持搜索歌曲、专辑、歌单、电台节目、电台
- 📄 **翻页选择**：搜索结果支持翻页、跳页、退出
- 🎨 **精美图片**：使用 PIL 绘制暗色主题搜索列表与歌词图片
- 🔗 **链接解析**：支持解析网易云标准链接和 163cn.tv 短链接
- 🎤 **音频发送**：自动下载音频并发送语音/文件
- 📝 **歌词获取**：获取歌词并以图片形式发送
- 🔐 **多种登录**：支持 Cookie、手机号、邮箱、游客登录

## 📦 安装

1. 将插件文件夹放入 AstrBot 的 `data/plugins/` 目录
2. 安装依赖：`pip install pyncm httpx Pillow pydantic cachetools`
3. 重启 AstrBot

## 🎯 使用方法

### 搜索指令

| 指令 | 别名 | 功能 |
|------|------|------|
| 点歌 [歌名/ID] | 网易云, wyy, 网易点歌, wydg, wysong | 搜索歌曲 |
| 网易专辑 [名/ID] | wyzj, wyal | 搜索专辑 |
| 网易歌单 [名/ID] | wygd, wypli | 搜索歌单 |
| 网易声音 [名/ID] | wysy, wyprog | 搜索电台节目 |
| 网易电台 [名/ID] | wydt, wydj | 搜索电台 |

### 操作指令

| 指令 | 说明 |
|------|------|
| 数字 | 选择对应序号 |
| 上一页 / P | 翻到上一页 |
| 下一页 / N | 翻到下一页 |
| P+数字 | 跳到指定页 |
| 退出 / E | 退出选择模式 |

### 其他指令

| 指令 | 别名 | 功能 |
|------|------|------|
| 解析 [链接] | resolve, parse, get | 解析网易云链接 |
| 歌词 [链接] | lrc, lyric, lyrics | 获取歌词图片 |
| 直链 [链接] | direct | 获取播放直链 |
| 点歌帮助 | ncm帮助, multincm帮助 | 显示帮助 |

### 自动解析

开启 `auto_resolve` 配置后，当用户发送包含网易云链接的消息时，会自动解析并发送歌曲。

## ⚙️ 配置项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| cookie_music_u | string | "" | 网易云 Cookie MUSIC_U 值 |
| phone | string | "" | 手机号（用于登录） |
| email | string | "" | 邮箱（用于登录） |
| password | string | "" | 登录密码 |
| anonymous | bool | false | 强制游客登录 |
| list_limit | int | 10 | 每页搜索结果数量（设为1变为单选） |
| send_as_file | bool | false | 以文件形式发送歌曲（而非语音） |
| auto_resolve | bool | false | 自动解析网易云链接 |
| ffmpeg_executable | string | "ffmpeg" | FFmpeg 路径 |

### 登录配置说明

- 推荐使用 Cookie 登录：登录网页版网易云音乐 → F12 打开开发者工具 → 在 Cookie 中找到 `MUSIC_U` 的值
- 如果不配置登录信息，将使用游客模式（部分 VIP 歌曲无法播放）
- 多种登录方式按优先级尝试：缓存 → Cookie → 手机号 → 邮箱 → 游客

## 🏗️ 项目结构

```
astrbot_plugin_multincm/
├── __init__.py          # 插件入口
├── main.py              # 主插件类（命令注册、交互逻辑）
├── api.py               # pyncm API 请求封装
├── models.py            # Pydantic 数据模型
├── data_source.py       # 搜索器与数据模型
├── login.py             # 网易云登录系统
├── renderer.py          # PIL 图片渲染
├── utils.py             # 通用工具函数
├── lrc_parser.py        # LRC 歌词解析器
├── metadata.yaml        # 插件元数据
├── _conf_schema.json    # 配置 Schema
├── requirements.txt     # 依赖列表
└── README.md            # 本文件
```

## 🔄 与 NoneBot 版本的差异

| 特性 | NoneBot 版本 | AstrBot 版本 |
|------|-------------|-------------|
| 渲染引擎 | Jinja2 + Playwright | PIL/Pillow |
| 会话等待 | nonebot_plugin_waiter | 消息匹配（Reply/正则） |
| 音乐卡片 | MusicShare / JSON 签名 | 降级为图文消息 |
| HTML 渲染 | Playwright 截图 | PIL 直接绘制 |
| 依赖注入 | Depends() | 类实例属性 |
| 配置系统 | .env + Pydantic | _conf_schema.json + dict |
| 音频转码 | silk-python (OB11) | 直接发送 MP3 |
| 消息撤回 | RecallContext | 不支持 |

## 📄 许可证

MIT License

## 🙏 致谢

- [nonebot-plugin-multincm] - 原始 NoneBot 插件
- [pyncm] - 网易云音乐 API 库
- [AstrBot] - AstrBot 框架
