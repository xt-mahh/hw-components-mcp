# hw-components-mcp

[![CI](https://github.com/xt-mahh/hw-components-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/xt-mahh/hw-components-mcp/actions/workflows/ci.yml)

元器件商城 MCP Server —— 让 agent（ZCode / Claude Code / Cursor 等任何 MCP 客户端）统一检索元器件信息：搜索料号、查参数、核对库存价格、下载数据手册、找替代料。

> 主仓库：[GitHub](https://github.com/xt-mahh/hw-components-mcp) | 国内镜像：[Gitee](https://gitee.com/xt-mahh_admin/hw-components-mcp)

## 接入平台

| 平台 | 接入方式 | 数据能力 | 限制 |
|---|---|---|---|
| **立创商城 LCSC** | lcsc.com 全球站 JSON API（免登录） | 搜索 / 详情 / 参数表 / 阶梯价(**美元**) / 库存 / 起订量 / 数据手册 / **替代料** | 接口未公开文档，已内置 ≥3s 请求间隔 |
| **京东元器件 ic.jd.com** | 搜索/详情页 SSR HTML 解析 | 搜索 / MPN / 品牌 / 封装 / 库存 / 起订量 | 页面价格需浏览器端签名，暂不可得（返回商品链接到页查看） |
| **华秋商城 hqchip.com** | 搜索页 SSR HTML 解析 | 搜索 / **人民币阶梯价** / 自营现货 / 数据手册直链 | **必须中国大陆网络出口**（海外 IP 直接拦截）；详情参数表需浏览器渲染，v1 不提供 |

> 说明：立创中文站 szlcsc.com 在 Aliyun WAF 后无法程序化访问，因此走全球站（价格单位为美元，库存为全球零售库存、非嘉立创贴片库存）。人民币价格请参考华秋渠道。

## 安装与运行

依赖 Python ≥ 3.10，用 [uv](https://docs.astral.sh/uv/) 管理：

```bash
cd hw-components-mcp
uv sync            # 安装依赖（含测试）
uv run hw-components-mcp          # 启动 stdio MCP server
uv run pytest      # 运行测试（29 个，全部离线）
uv run python scripts/smoke.py    # 真实平台冒烟（低频请求）
```

## 接入 ZCode

把以下片段合并进 `~/.zcode/cli/config.json`（用户级，所有工作区可用）；或放进项目的 `.zcode/config.json`（团队共享）。完整示例见 `examples/zcode-config.json`。

```json
{
  "mcp": {
    "servers": {
      "hw-components": {
        "command": "uv",
        "args": [
          "--directory", "/path/to/hw-components-mcp",
          "run", "hw-components-mcp"
        ]
      }
    }
  }
}
```

> `--directory` 替换为你本机的项目绝对路径（Windows 下用正斜杠 `C:/...` 即可）。也可以先 `pip install .` 安装，然后 command 直接写 `hw-components-mcp`。

重启 ZCode 会话后，Settings → MCP 应显示 `hw-components` 已连接。

## 工具列表

| 工具 | 说明 |
|---|---|
| `list_platforms()` | 平台元信息：币种口径、库存口径、区域要求 |
| `search_components(keyword, platform, limit, in_stock_only)` | 关键词/型号搜索；`platform="all"` 聚合三平台；立创支持直接搜 C 编号 |
| `get_component_detail(product_id, platform)` | 完整参数表、阶梯价、库存、起订量、数据手册链接、替代料 |
| `check_bom(bom_lines, platform)` | BOM 批量核查：多行「MPN 数量」→ 逐行库存 / 按量单价 / 缺口 / 是否精确匹配 |
| `download_datasheet(product_id, platform, save_dir)` | 下载数据手册 PDF 到本地，返回绝对路径（配合 pdf 工具精读） |
| `find_alternatives(product_id, platform)` | 替代料查询（LCSC 官方标注） |

### 典型用法

```
用户：帮我找一个 0603 10k 1% 的电阻，三家平台比一下
  → search_components("0603 10k 1%", platform="all")

用户：STM32F103C8T6 的具体参数和手册
  → search_components("STM32F103C8T6") → get_component_detail("C8734")
  → download_datasheet("C8734")

用户：核一下这份 BOM 在立创的库存：STM32F103C8T6 x250, LM358DR x100
  → check_bom("STM32F103C8T6 250\nLM358DR 100", platform="lcsc")

用户：C8734 停产了，有替代料吗
  → find_alternatives("C8734")
```

## 配置项（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `HWC_MIN_INTERVAL_LCSC` | `3.0` | wmsc.lcsc.com 最小请求间隔（秒），勿调小 |
| `HWC_MIN_INTERVAL` | `1.0` | 其他域名默认间隔 |
| `HWC_HTTP_TIMEOUT` | `20` | 请求超时（秒） |
| `HWC_NO_CACHE` | `0` | 置 1 关闭 TTL 缓存（搜索 5 分钟 / 详情 15 分钟） |

## 架构

```
src/hw_components_mcp/
├── server.py        # FastMCP 入口，六个工具
├── models.py        # 统一数据模型（ComponentSummary/Detail、PriceTier、BomReport…）
├── http.py          # 共享 httpx 客户端：分域限流 / 重试退避 / TTL 缓存
└── providers/
    ├── base.py      # Provider 抽象 + ProviderError
    ├── lcsc.py      # wmsc.lcsc.com JSON API
    ├── jd.py        # ic-search / ic-item SSR HTML 解析
    └── hqchip.py    # hqchip 搜索/详情 SSR HTML 解析
```

新增平台（如 ICKEY 官方开放平台、嘉立创 open.jlc.com）只需实现 `Provider.search()/get_detail()` 并注册进 `providers/__init__.py`。

测试策略：解析器单测全部基于 `tests/fixtures/` 里实录的 JSON/HTML（离线运行，不触发风控）；`tests/test_http.py` 用 `httpx.MockTransport` 验证重试与缓存；`tests/test_tools.py` 用假 Provider 验证工具编排逻辑。

## 已知限制与风险

- **接口变更风险**：三个平台均为非官方接口/页面。京东、华秋 HTML 改版会导致解析失败——此时工具返回明确错误（附原始链接），fixture 回归测试可在升级时快速定位。LCSC 的 JSON 接口自 2024 年以来多次迁移（`/wmsc/*` → `/ftps/*`），如再迁移只需改 `lcsc.py` 中的 BASE/路径。
- **限流**：LCSC 官方口径为 200 次/分钟、1000 次/天（Agent API），本服务按 ≥3s 间隔 + 缓存访问，正常交互式使用远低于限额。请勿用本服务做批量爬取。
- **合规**：仅访问各平台免登录公开页面/接口，低频、带缓存、无批量导出；数据归各平台所有，请遵守平台条款。
- **价格口径**：LCSC=美元（全球站），华秋=人民币（自营），京东=无价（SSR 不含）。

## 多平台仓库同步

主仓库为 GitHub，Gitee 为国内镜像。维护者日常双推：

```bash
git push origin main && git push gitee main
```

## License

MIT
