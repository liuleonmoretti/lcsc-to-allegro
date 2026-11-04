# LC2A · 立创 / KiCad 转 Allegro 16.6

将立创 **C 编号**对应的 PCB 封装，或本地 **KiCad `.kicad_mod`** 文件，转换为 Cadence Allegro 16.6 原生 `.pad`、`.dra` 和 `.psm`。

这是一个供下载使用的 **Windows 本地命令行工具**。生成原生封装需要你自己安装并具备可用许可的 Cadence Allegro 16.6；仓库不包含 Cadence 软件，也没有在线转换服务。

**[下载源码 ZIP](https://github.com/liuleonmoretti/lcsc-to-allegro/archive/refs/heads/main.zip)** · **[安装指南](docs/installation.md)** · **[使用说明](docs/usage.md)** · **[第一篇项目博客](docs/blog/2026-11-03-my-first-open-source-project.md)**

> 当前程序版本：`0.2.0`。下载包是源码，解压后需要安装 Python 依赖，并按自己的 Cadence 安装位置修改配置。尚未提供独立 EXE。

## 可以做什么

- 根据立创 C 编号获取封装，或直接读取本地 `.kicad_mod`；不需要另外安装 KiCad。
- 转换当前支持的贴片焊盘、圆孔镀通孔焊盘和轮廓图形，保留引脚编号。
- 生成原生焊盘和封装，并在独立验证流程中重新读取原生文件、检查几何与引脚信息。
- 为每次任务保存源文件、转换参数、日志和校验记录，便于追查失败原因。
- 验证通过后，将封装发布到指定的本地 Allegro 库。

**支持范围有边界。** 圆角矩形、自定义焊盘、槽孔、非镀通孔等未支持的输入会明确报错；不是所有 C 编号都能转换。本工具不生成 OrCAD Capture `.olb` 符号，也不转换整块 PCB。详见 [支持范围](docs/supported-features.md)。

## 快速开始

以下命令在 **Windows PowerShell** 中执行，先进入解压后的项目目录。

### 1. 安装依赖并设置 Cadence 路径

本次发布使用 Windows、Python 3.14 和 Allegro 16.6 环境检查。先确认 `python --version`，然后执行：

```powershell
Copy-Item .\config.example.json .\config.json
notepad .\config.json
```

将配置中的四个路径改为自己的 Cadence 安装路径，保存后执行：

```powershell
.\setup.ps1
.\lc2a.ps1 doctor
```

`doctor` 的 `checks` 应全部为 `true`。遇到脚本执行策略限制，见 [安装指南](docs/installation.md)。只想阅读代码和运行不依赖 Cadence 的测试，可以使用 `./setup.ps1 -SkipDoctor`。

### 2. 先用仓库内的示例跑通流程

```powershell
.\lc2a.ps1 convert --input .\tests\header_1x2.kicad_mod --name DEMO_HEADER_1X2 --refdes J
```

终端输出的 `job` 是本次任务目录。将下面占位路径替换为该值，再执行独立验证：

```powershell
.\lc2a.ps1 verify --job "这里替换为上一条命令输出的 job 路径"
```

`convert` 成功只表示完成生成及转换阶段的检查，**还要执行 `verify`**。生成后的文件位于该任务目录的 `native` 子目录；在完成验证和人工核对之前，不要直接拿去投板。

### 3. 换成自己的封装

本地文件：

```powershell
.\lc2a.ps1 convert --input "D:\Footprints\part.kicad_mod" --name MY_PART --refdes U
```

按 C 编号下载：

```powershell
.\lc2a.ps1 convert --lcsc C123456 --name MY_PART --refdes U
```

`C123456` 是格式示例，请替换成自己的器件编号。能否成功取决于上游数据可访问性及封装几何是否处于支持范围，不能据此假定某一编号已通过验证。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [安装指南](docs/installation.md) | Python、Cadence 路径、PowerShell 和常见安装问题 |
| [使用说明](docs/usage.md) | 转换、验证、输出目录和本地库发布 |
| [支持范围](docs/supported-features.md) | 已支持和明确拒绝的几何，使用边界 |
| [程序结构](docs/architecture.md) | 数据流、模块职责和验证设计 |
| [验证记录](docs/validation.md) | 本次公开包检查与历史本机集成验证的区别 |
| [厂家资料要求](docs/datasheets/README.md) | 三个可选修正规则所需的指定 PDF 版本和哈希 |
| [测试说明](tests/README.md) | 可直接运行的测试及未随包分发的本机测试条件 |
| [第一篇博客](docs/blog/2026-11-03-my-first-open-source-project.md) | 为什么做这个项目、工作流程和下一步 |

## 目录

```text
lcsc-to-allegro/
├── lc2a.ps1                 # 命令行入口
├── setup.ps1                # 建立 Python 环境和安装依赖
├── test.ps1                 # 运行公开包中的独立测试子集
├── config.example.json      # 按自己的安装位置复制并修改
├── requirements.txt         # 固定版本的 Python 依赖
├── src/                     # 转换、验证、库发布实现
├── scripts/                 # 公开包测试入口
├── tests/                   # 测试源码和两个合成封装示例
└── docs/                    # 安装、使用、验证、博客和第三方许可
```

运行后产生的 `.venv/`、`config.json`、`jobs/`、`outputs/` 和 `work/` 不加入 Git。安装目录、厂家 PDF、原生二进制测试样本和个人历史工程不随本仓库分发。

## 开发与反馈

```powershell
.\test.ps1
```

该命令运行不依赖 Cadence 安装及未公开历史样本的测试子集，不能替代真实 Allegro 集成验证。欢迎通过 [Issues](https://github.com/liuleonmoretti/lcsc-to-allegro/issues) 提交问题，附程序版本、输入类型和错误日志；请先移除账号、个人路径和未获授权公开的工程资料。

## 许可证与致谢

项目源码按 [GNU AGPL v3](LICENSE) 分发。第三方依赖保留各自许可证，见 [第三方说明](THIRD_PARTY_NOTICES.md)。工具使用 `easyeda2kicad`、`sexpdata` 和 `Shapely`；不代表或隶属于嘉立创、KiCad 或 Cadence。

封装数据、厂家图纸及 Cadence 软件的权利仍属于其各自权利人。几何验证不等于完整可制造性评审或整板 DRC，通过后仍需结合厂家图纸、工艺和实际设计核对。
