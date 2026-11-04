# 第三方依赖与资料

LC2A 项目源码使用 [GNU AGPL v3](LICENSE)。依赖通过 `requirements.txt` 安装，仓库不捆绑 `.venv` 或 Cadence 安装文件。

| 依赖 | 固定版本 | 许可副本 |
| --- | --- | --- |
| [easyeda2kicad](https://github.com/uPesy/easyeda2kicad.py) | 1.0.1 | [AGPL 文本](docs/third_party/easyeda2kicad-LICENSE) |
| [sexpdata](https://github.com/jd-boyd/sexpdata) | 1.0.2 | [许可文本](docs/third_party/sexpdata-LICENSE) |
| [Shapely](https://github.com/shapely/shapely) | 2.1.2 | [许可文本](docs/third_party/shapely/LICENSE.txt)、[GEOS 说明](docs/third_party/shapely/LICENSE_GEOS)、[Windows 分发说明](docs/third_party/shapely/LICENSE_win32) |

上述许可文件保留各自原始声明。安装的间接依赖和二进制组件也应遵守其自身许可；重新分发打包程序前应核对实际分发内容。

Cadence / Allegro、嘉立创 / LCSC / EasyEDA、KiCad 等名称用于描述兼容对象和数据来源，本项目与这些产品或公司没有官方隶属关系。

项目许可证不重新授权第三方封装、厂家 PDF、原始工程或 Cadence 软件。使用或再分发这些资料时，应根据其来源另行确认权利。厂家 PDF 和原生二进制历史测试样本未包含在本仓库中。
