# 测试说明

[返回首页](../README.md)

## 公开包可直接运行的测试

安装 Python 依赖后：

```powershell
.\test.ps1
```

入口 `scripts/run_portable_tests.py` 显式选择已有测试：

- `test_converter`：坐标、中心、引脚间距及错误输入。
- `test_source_faults`：源几何故障注入和转换/验证两条路径的拒绝行为。
- `test_raw_graphic_values`：上游原始数值、图形精度和标准化一致性。
- `test_selftest_inputs`：测试报告的输入快照与增删改检测。
- `test_process_runtime`：受控子进程超时、错误诊断和编码。
- `test_enclosure_exact.Enclosure`、`TestBoardExtents`：几何包围与测试板范围。

它们使用仓库内的合成封装或临时构造的数据，不启动 Cadence，不需要厂家 PDF。被选中的测试发生跳过也会使入口返回失败。

## 为什么不直接运行全部测试发现

本目录保留了完整的原测试源码供研究，但其他测试中有依赖历史任务、原生二进制样本、厂家 PDF 或本机 Cadence 的项目。这些原始资料没有随公开包分发。

因此直接运行 `unittest discover` 或 `src/selftest.py`，可能因缺少输入失败。**这不等于这些测试已在公开包中通过，也不能靠删除断言或把缺文件改成成功解决。**

要复现完整集成流程，需要单独准备对应受许可的软件和原始样本，并核对测试引用的输入。公开包当前承诺可直接复现的范围仅限上述子集，以及满足安装要求后自己执行的示例 `convert` / `verify`。

`header_1x2.kicad_mod` 和 `sot23_5.kicad_mod` 是合成几何示例，不是生产器件推荐封装。具体检查结果见 [验证记录](../docs/validation.md)。
