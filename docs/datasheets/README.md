# 可选厂家规则的资料要求

[返回使用说明](../usage.md)

只有使用 `--profile` 时才需要本页列出的厂家 PDF。普通本地输入与未启用规则的 C 编号路径不依赖这三个文件。

本项目不重新分发厂家 PDF。请自行取得有权使用的、与已评审版本一致的文件，按下面的文件名放在本目录。程序检查**完整文件字节的 SHA-256**；新版 PDF、重新保存或打印生成的 PDF 即使肉眼相似，也可能不匹配。

| 规则 | 器件 | 文件名 | 评审版本 |
| --- | --- | --- | --- |
| `C8734` | STM32F103C8T6，LQFP48 | `STM32F103C8.pdf` | ST DS5319 Rev 20，2025-07，PDF 第 98–100 页 |
| `C53568` | 1N4007RLG，DO-41 | `1N4007.pdf` | onsemi 1N4001/D Rev 13，2012-10，第 1、7 页；CASE 59-10 ISSUE U |
| `C62715` | LITEON LTV-817-A，标准 DIP4 | `C62715_LTV817A.pdf` | January 2010，15 页版本，第 2、11 页，未标明独立修订号 |

对应哈希：

```text
STM32F103C8.pdf
1e54ce18e6c34c5b4a78986f26e245e143748c721009fc9af5953795c30686bc

1N4007.pdf
3967a81f94842515b1ff4258fc779a87ba9c7a11f464cd974dd0eba1941c84b7

C62715_LTV817A.pdf
616ee1f8582185fc8562fb98e868051147a00d58ac8c76baf75251fdbbb689ce
```

资料参考入口：[ST 数据手册](https://www.st.com/resource/en/datasheet/stm32f103c8.pdf)、[onsemi 数据手册](https://www.onsemi.com/pdf/datasheet/1n4001-d.pdf)、[LCSC LTV-817-A 资料](https://datasheet.lcsc.com/datasheet/pdf/02b3eef263084ccda653b96b967d41b5.pdf?productCode=C62715)。这些地址可能更新，**不保证现在下载到的字节就是已评审版本**。尤其 `1N4007.pdf` 的历史评审文件由本机用户提供，参考地址不是该旧文件的可复现分发承诺。

可以用 PowerShell 自查：

```powershell
Get-FileHash .\docs\datasheets\STM32F103C8.pdf -Algorithm SHA256
```

没有匹配资料时应停止使用对应规则；不要篡改哈希常量或验证报告来绕过检查。新增图纸版本需要重新评审几何、更新规则和运行原生验证。
