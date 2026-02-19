# Research Team 前置调研清单

## 新策略项目启动前必须提供的信息

| Field | Value |
|-------|-------|
| Version | 1.0 |
| Date | 2026-02-10 |
| Status | Final |
| 用途 | 供 Research Team 在新策略立项时，提前收集开发所需的技术信息 |
| 背景 | 基于 HyENA × GRVT Funding Rate Arbitrage 项目开发过程中的经验教训总结 |

---

## 目录

1. [概述](#1-概述)
2. [Checklist 总览](#2-checklist-总览)
3. [详细要求](#3-详细要求)
4. [交付模板](#4-交付模板)
5. [常见踩坑案例](#5-常见踩坑案例)

---

## 1. 概述

### 1.1 为什么需要这份文档

在上一个项目（跨交易所 Funding Rate 套利）的开发中，我们遇到了大量因信息缺失或信息错误导致的返工：

- 社区代码中的参数值与官方不一致（TimeInForce 枚举值错误），导致签名失败
- 费率单位不统一（百分比 vs 小数、1h vs 8h），导致计算逻辑反复修改
- 认证流程涉及双域名 + Cookie 传递，没有文档指引，纯靠试错
- Python 版本过新（3.14），部分核心库（aiohttp）无法安装，发现时已写完大量异步代码

**这些问题 80% 可以通过事先调研避免。** 本文档定义了 Research Team 在新策略项目立项时需要提前收集并交付的标准化信息清单。

### 1.2 适用范围

任何涉及以下场景的新策略项目：
- 对接新交易所 API（行情 / 交易）
- 涉及链上签名（EIP-712、EIP-191 等）
- 需要自动化下单 / 仓位管理

---

## 2. Checklist 总览

| # | 类别 | 优先级 | 简述 |
|---|------|--------|------|
| A | API 文档 & 响应样本 | **P0** | 官方文档链接 + 每个关键接口的真实响应 JSON |
| B | 精度与交易限制 | **P0** | 最小下单量、tick size、小数位数、费率单位 |
| C | 认证流程 | **P0** | 完整的认证步骤、所需凭证清单 |
| D | 运行环境 | **P1** | Python 版本、已安装包、OS 信息 |
| E | 可运行的最小示例 | **P1** | 至少一个成功调用的 curl / Python 代码片段 |
| F | 账户与环境状态 | **P1** | 余额、持仓、测试网 vs 主网 |
| G | 已知坑点记录 | **P2** | 社区 / 文档中已知的错误或不一致 |

---

## 3. 详细要求

### A. API 文档 & 响应样本 (P0)

**目标：** 让开发者无需猜测即可正确构造请求和解析响应。

需要提供：

#### A1. 官方 API 文档

- [ ] 每个交易所的**官方** REST API 文档链接（不接受社区 gist 或第三方整理）
- [ ] 如有 WebSocket API，同样提供官方文档链接
- [ ] 如有官方 SDK（Python），提供 PyPI 包名和 GitHub 链接

#### A2. 关键接口的真实响应

对以下每个接口类型，提供一个**真实的请求 + 响应**（可脱敏，但字段结构和数据类型必须真实）：

| 接口类型 | 说明 |
|----------|------|
| 获取行情 / Ticker | 包含价格、funding rate、OI 等 |
| 获取 Orderbook | 注意 depth 参数限制 |
| 获取 Funding Rate 历史 | 注意分页、时间范围参数 |
| 查询余额 / 账户信息 | 注意是否有多钱包合并逻辑（如 HyENA 的 Spot + Perps） |
| 下单 | 完整的请求 body + 成功响应 |
| 查询订单状态 | |
| 查询持仓 | |

**格式要求：**

```
## [交易所名] - [接口名]

### 请求
curl -X POST https://api.example.com/v1/order \
  -H "Content-Type: application/json" \
  -d '{"instrument": "BTC_USDT_Perp", "side": "buy", ...}'

### 响应 (HTTP 200)
{
  "order_id": "123456",
  "status": "filled",
  ...
}

### 备注
- depth 参数只能传 10，其他值返回 400
- funding_rate 字段单位是百分比，不是小数
```

---

### B. 精度与交易限制 (P0)

**目标：** 避免因精度错误导致签名失败或下单被拒。

每个交易所需要填写以下表格：

| 参数 | 值 | 说明 |
|------|-----|------|
| **最小下单量** (min_size) | 例: 0.001 BTC | |
| **最小名义价值** (min_notional) | 例: $100 USDT | |
| **价格精度** (tick_size) | 例: $0.1 | |
| **数量精度** (szDecimals) | 例: 5 (即 0.00001) | |
| **内部精度** (base_decimals) | 例: 9 (用于签名计算) | |
| **最大杠杆** | 例: 20x | |
| **杠杆设置方式** | API / 仅前端 UI | 如果只能前端设置，代码侧需做 guard |
| **Funding Rate 单位** | 百分比 or 小数 | 0.01 = 0.01% 还是 0.01 = 1%？ |
| **Funding Rate 周期** | 1h / 8h / 其他 | API 返回的是哪个周期的费率？ |
| **结算频率** | 每 1h / 每 8h | 实际扣款频率 |
| **手续费** (maker/taker) | 例: 0.02% / 0.05% | |

**特别注意：**
- 如果签名计算需要用 `Decimal` 而非 `float`，请标注
- 如果存在"API 文档写的"和"实际行为"不一致的情况，以实际为准并标注

---

### C. 认证流程 (P0)

**目标：** 开发者能一次性跑通认证，不需要反复试错。

需要提供：

#### C1. 认证方式总览

| 交易所 | 认证方式 | 是否需要链上签名 |
|--------|---------|----------------|
| 例: GRVT | API Key + Cookie Session + EIP-712 | 是（每笔订单） |
| 例: HyENA | Wallet Private Key + EIP-712 | 是（每笔订单） |

#### C2. 完整认证步骤

每个交易所提供从零到成功下单的**完整步骤**，包括：

- [ ] 如何获取 API Key / 凭证（哪个页面、哪个按钮）
- [ ] 认证请求的完整 HTTP 流程（含 headers、cookies 传递）
- [ ] 如有多域名（如 GRVT 的 edge.grvt.io → trades.grvt.io），明确说明域名切换逻辑
- [ ] 如有签名（EIP-712），提供签名结构体的字段定义和 TypeHash
- [ ] Token / Session 的有效期和刷新机制

#### C3. 所需凭证清单

| 凭证名 | 来源 | 格式 | 示例（脱敏） |
|--------|------|------|-------------|
| 例: GRVT_API_KEY | GRVT 后台 → Settings → API | 字符串 | `grvt_ak_...` |
| 例: HYENA_PRIVATE_KEY | 钱包导出 | 0x 开头的 hex | `0xabcd...` |

---

### D. 运行环境 (P1)

**目标：** 避免在开发中途发现环境不兼容。

- [ ] Python 版本：`python3 --version` 的输出
- [ ] 已安装包列表：`pip list` 的输出（或 `requirements.txt`）
- [ ] 操作系统：`uname -a` 的输出
- [ ] 是否有特殊网络限制（VPN、代理、IP 白名单）

**特别注意：**
- 如果某些常用库在当前 Python 版本下无法安装（如 Python 3.14 下 aiohttp 无 wheel），提前标注
- 如果交易所 API 有 IP 白名单要求，提前配置并确认

---

### E. 可运行的最小示例 (P1)

**目标：** 一个能直接复制粘贴运行的代码片段，验证 API 连通性。

每个交易所至少提供以下**两个**可运行示例：

#### E1. 公开接口示例（无需认证）

```python
# 获取 [交易所] BTC 行情
import requests
resp = requests.post("https://api.xxx.com/info", json={...})
print(resp.json())
```

#### E2. 认证接口示例（需凭证）

```python
# 在 [交易所] 查询账户余额
# 前置条件：设置环境变量 XXX_API_KEY=...
import os, requests
api_key = os.environ["XXX_API_KEY"]
# ... 完整的认证 + 请求代码 ...
print(resp.json())
```

**要求：**
- 代码必须是**实际运行成功过**的，不是从文档复制的伪代码
- 如有签名逻辑，包含完整的签名代码（不要省略 hash 计算步骤）

---

### F. 账户与环境状态 (P1)

**目标：** 让开发者知道当前的操作环境和约束。

- [ ] 各交易所当前余额（USDT / USDC / USDe 等）
- [ ] 当前持仓情况（如有）
- [ ] 明确标注：**测试网** or **主网**
- [ ] 如果是测试网，提供测试网 endpoint（通常与主网不同）
- [ ] 各交易所账户的 KYC / 交易权限状态

---

### G. 已知坑点记录 (P2)

**目标：** 避免重复踩别人已经踩过的坑。

记录格式：

| # | 交易所 | 问题描述 | 正确做法 | 来源 |
|---|--------|---------|---------|------|
| 1 | GRVT | 社区 gist 中 IOC=2 是错的 | IOC=3（以官方 SDK 为准） | 实测 |
| 2 | GRVT | Book API depth 只能传 10 | 固定 depth=10 | 实测 |
| 3 | HyENA | hyna:BTC 不在 metaAndAssetCtxs 里 | 用 `dex: "hyna"` 查询 | 实测 |
| 4 | GRVT | float 精度丢失导致签名失败 | 用 Decimal(str(x)) | 实测 |
| 5 | HyENA | BTC tick size 是 $1，不是 $0.1 | `round(price, 0)`；orderbook 价格均为整数 | 实测 (422 error) |
| 6 | HyENA | SDK 返回 `{'status':'ok'}` 即使订单被拒 | 检查 `response.data.statuses[].error` | 实测 (单腿暴露) |
| 7 | GRVT | 没有 API 设置杠杆倍数；默认可达 50x | 必须在前端 UI 手动设置杠杆 | 实测 + 官方文档 |
| 8 | HyENA | builder address 格式必须是标准 0x 地址 | 不能有额外前缀（如 `0xpyth...`）；会导致 422 | 实测 |

---

## 4. 交付模板

Research Team 交付时，请按以下目录结构组织：

```
research_output/
├── README.md                    # 总览 + 各交易所状态摘要
├── {exchange_name}/
│   ├── api_docs.md              # A: 官方文档链接 + 响应样本
│   ├── trading_params.md        # B: 精度与交易限制表格
│   ├── auth_flow.md             # C: 认证流程详解
│   ├── examples/
│   │   ├── public_api.py        # E1: 公开接口示例
│   │   └── auth_api.py          # E2: 认证接口示例
│   └── known_issues.md          # G: 已知坑点
├── environment.md               # D: 运行环境信息
└── account_status.md            # F: 账户与环境状态
```

---

## 5. 常见踩坑案例

以下案例来自 HyENA × GRVT 项目的实际开发过程，供 Research Team 理解为什么每项 Checklist 都重要。

### 案例 1：费率单位不一致 → 收益计算错误

**问题：** GRVT 的 `funding_rate_8h_curr` 返回的是百分比值（0.01 表示 0.01%），而 HyENA 返回的是小数（0.0001 表示 0.01%）。开发时未区分，导致跨交易所 spread 计算差了 100 倍。

**本可避免的方式：** Checklist B 中明确标注每个交易所的费率单位。

### 案例 2：社区代码 TimeInForce 值错误 → 签名失败

**问题：** 参考了社区 gist（minhbsq/40842859）的 EIP-712 签名代码，其中 `IOC=2`。实际官方 SDK 中 `IOC=3`。错误的枚举值导致签名和 payload 不匹配，返回 "Signature does not match payload"，调试了数小时。

**本可避免的方式：** Checklist A 中要求只使用官方文档；Checklist G 中记录此坑点。

### 案例 3：Python 3.14 无 aiohttp wheel → 架构返工

**问题：** 开发机器上 Python 版本为 3.14.2，aiohttp 尚无预编译 wheel，编译安装失败。此时已写完大量基于 aiohttp 的异步代码，被迫改为 `asyncio.to_thread` + `requests` 的方案。

**本可避免的方式：** Checklist D 中提前确认 Python 版本和可用库。

### 案例 4：GRVT 双域名认证 → 反复 403

**问题：** GRVT 的认证需要在 `edge.grvt.io` 登录获取 cookie，然后将 cookie 传递到 `trades.grvt.io` 进行交易。且 cookie 必须通过 `cookies=` 参数传递，不能放在 `Cookie:` header 中。这个流程在文档中没有明确说明。

**本可避免的方式：** Checklist C 中要求提供完整的多域名认证流程。

### 案例 5：HyENA Spot + Perps 合并余额 → 余额查询遗漏

**问题：** HyENA 支持 Spot + Perps 合并保证金。只查 `clearinghouseState` 会遗漏 Spot 钱包中的 USDe。需要同时查询 `spotClearinghouseState` 并合计。

**本可避免的方式：** Checklist A2 中要求提供余额查询的完整响应样本，包含备注说明。

---

## 附录：Checklist 一页纸版本（可打印）

```
项目名称：_______________     日期：_______________
Research 负责人：_______________

[ ] A1. 官方 API 文档链接（每个交易所）
[ ] A2. 关键接口真实响应样本（行情/下单/余额/持仓/funding）
[ ] B.  精度与交易限制参数表（min_size/tick/decimals/费率单位）
[ ] C1. 认证方式总览
[ ] C2. 完整认证步骤（含多域名/签名结构）
[ ] C3. 所需凭证清单
[ ] D.  运行环境信息（Python版本/pip list/OS）
[ ] E1. 公开接口可运行示例
[ ] E2. 认证接口可运行示例
[ ] F.  账户状态（余额/持仓/测试网or主网）
[ ] G.  已知坑点记录

签字确认：_______________
```
