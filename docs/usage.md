# 使用说明

[返回首页](../README.md) · [先完成安装](installation.md)

## 基本流程

```text
输入封装 → convert 生成 → verify 独立验证 → 人工核对 → publish 到本地库
```

每次转换都会创建独立的 `jobs/<时间>_<名称>_<随机后缀>/`。终端 JSON 输出中的 `job` 字段给出完整位置。

## 先试本地示例

```powershell
.\lc2a.ps1 convert --input .\tests\header_1x2.kicad_mod --name DEMO_HEADER_1X2 --refdes J
```

生成后，把实际输出目录放入变量，执行验证：

```powershell
$jobPath = "这里替换为 convert 输出的 job 路径"
.\lc2a.ps1 verify --job $jobPath
```

示例是为测试编写的合成封装，不附带任何真实器件的投板承诺。

## 输入选项

`--lcsc` 和 `--input` 二选一：

```powershell
.\lc2a.ps1 convert --input "D:\Footprints\part.kicad_mod" --name MY_PART --refdes U
.\lc2a.ps1 convert --lcsc C123456 --name MY_PART --refdes U
```

第二行的 C 编号是格式示例。工具会检查下载数据身份、上游几何和引脚信息；网络下载成功不意味着该封装已受支持。

`--name` 是原生文件名：ASCII 字母开头，长度 1–31，使用字母、数字、下划线；不要使用 Windows 设备保留名。`--refdes` 是 1–4 个大写英文字母，例如 `U`、`J`、`D`。中文展示名可用 `--label`，不替代原生文件名。

## 只生成中间文件

```powershell
.\lc2a.ps1 convert --input .\tests\header_1x2.kicad_mod --name DEMO_PREPARE --refdes J --prepare-only
```

该模式适合检查解析结果，不启动 Cadence。状态 `prepared_only` 表示只有中间文件，不能当作已经生成、验证或可发布的原生封装。

## 原点与尺寸

默认依据受支持的 `F.Fab` 本体轮廓确定中心；不存在可靠依据时会失败。只有在已核对厂家图纸的情况下，才显式指定 `--center X Y` 或 `--body-bounds XMIN YMIN XMAX YMAX`，单位为毫米。

不要为了消除错误随意填 `--center 0 0`。引脚中心、丝印中心和封装本体中心可能不同。

## 可选厂家修正规则

当前有三个显式规则：`C8734`、`C53568`、`C62715`。使用前必须自行准备对应的指定版本 PDF，见 [厂家资料要求](datasheets/README.md)。

```powershell
.\lc2a.ps1 convert --lcsc C8734 --name STM32F103C8T6_LQFP48 --refdes U --profile C8734
```

上例只有满足资料及数据检查条件才能完成。修正规则提供本体中心和阻焊参数，不应再叠加手写 `--center` 或 `--body-bounds`。其他 C 编号不能套用这三个规则。

## 输出和状态

| 内容 | 位置或含义 |
| --- | --- |
| 源输入与下载记录 | 任务目录中的 `source.kicad_mod` 或 `source/` |
| 中间几何 | `model.json` |
| 焊盘量化记录 | `pad_quantization.json` |
| 原生构建脚本 | `build.il` |
| 转换状态与文件哈希 | `status.json` |
| 原生文件、构建日志 | `native/` |
| 独立验证证据 | `verification/` |
| `prepared_only` | 只完成预处理 |
| `built_and_geometry_checked` | 原生构建阶段完成，仍须独立 `verify` |
| `failed` | 任务失败，应查看 `error` 及保留日志 |

验证关注引脚位置与编号、焊盘、阻焊、锡膏、钻孔、图形、上下板面变换和原生日志等。它不代替完整的制造评审或整板 DRC。

## 发布到本地库

先完成 `verify`，再人工核对封装和厂家图纸。以下命令会修改明确指定的本地库目录，请选用自己的库路径：

```powershell
.\lc2a.ps1 publish --job $jobPath --category Connector --library "D:\Allegro_Lib" --review-note "填写本次实际核对的图纸和结论"
```

`--review-note` 应记录实际检查内容，不要把上面的示例文案当成已完成评审。原生文件、证据和索引由库发布模块处理。`publish` 在此指本地 EDA 库发布，与上传 GitHub 无关。

进阶的 `library-test`、`cleanup-test`、`recover-test` 用于带收据的库事务试验和恢复；使用前应阅读 `src/library.py`，不要把它们当成通用删除命令。
