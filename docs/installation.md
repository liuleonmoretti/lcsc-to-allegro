# 安装指南

[返回首页](../README.md)

## 环境要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows；原生生成与验证依赖 Windows 版 Cadence |
| Python | 本次公开包验证使用 Python 3.14；其他版本未在本次发布中验证 |
| Cadence | 已安装且许可可用的 Allegro 16.6，包含 PCB Editor、Pad Designer 及相关命令行工具 |
| 网络 | 首次安装 Python 依赖需要网络；按 C 编号取数据需要访问上游服务 |
| 本地输入 | `.kicad_mod`；不需要安装 KiCad 应用 |

只查看源码、运行公开包独立测试或生成中间文件时，不需要启动 Cadence。生成 `.pad/.dra/.psm` 及独立原生验证仍需要 Cadence。

## 下载与配置

1. 从仓库首页点击 **Code → Download ZIP**，解压到自己的工作目录，例如 `D:\Tools\lcsc-to-allegro`。
2. 在这个目录打开 PowerShell，运行 `python --version`。如果系统找不到 Python，请先安装并让 `python` 命令可用。
3. 执行：

```powershell
Copy-Item .\config.example.json .\config.json
notepad .\config.json
```

按自己的安装位置修改下面四项。JSON 中建议使用 `/` 作为路径分隔符：

| 配置项 | 指向什么 |
| --- | --- |
| `cadence_bin` | 包含 `allegro.exe`、`create_sym.exe`、`extracta.exe`、`pad_designer.exe` 的目录 |
| `seed` | Cadence 自带的 `smr0603.dra` 模板 |
| `pad_report_view` | `pad_rep_view.txt` 报告视图文件 |
| `board_seed` | `Altium_Template1.brd` 测试板模板 |

这些文件来自用户自己的 Cadence 安装，本项目不提供替代安装包。`config.example.json` 只是路径示例；`config.json` 是本机配置，不要提交到仓库。

## 建立 Python 环境

```powershell
.\setup.ps1
```

脚本在项目内建立 `.venv`，安装 `requirements.txt` 中固定版本的依赖，再运行 `doctor`。脚本不会自动安装 Cadence。

如果尚未安装 Cadence，只准备研究代码和运行独立测试：

```powershell
.\setup.ps1 -SkipDoctor
.\test.ps1
```

如果 PowerShell 提示禁止运行脚本，在当前窗口执行以下命令后重试；它只影响这个 PowerShell 进程：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## 检查安装

```powershell
.\lc2a.ps1 doctor
```

`checks` 中所有项都应为 `true`。该检查验证路径、可执行文件和临时目录，不代表已经完成真实原生编译或证明 Cadence 许可一定可用；仍需用示例执行 `convert` 和 `verify`。

Cadence 原生流程使用独立的 ASCII 临时目录。若临时目录不可写或路径不满足要求，可在 `config.json` 中增加 `"native_temp_root": "C:/lc2a-temp"`，并确保该目录可写。不要将任务指向正在编辑的原始工程。

## 常见问题

- **找不到 `.venv/Scripts/python.exe`**：先运行 `setup.ps1`，确认依赖安装没有报错。
- **`doctor` 某项为 `false`**：按字段名修正 `config.json`，不要只修改盘符后假定文件存在。
- **Cadence 启动或许可失败**：先确认自己能正常启动对应的 Allegro 16.6 工具，再检查任务日志。
- **下载失败**：C 编号路径依赖上游服务；可以改用自己有权使用的本地 `.kicad_mod` 排查转换部分。
- **未支持的几何报错**：参见 [支持范围](supported-features.md)，不要删除校验强行生成。
- **厂家 PDF 哈希不匹配**：可选 `--profile` 只接受已评审的指定文件，参见 [资料要求](datasheets/README.md)。
